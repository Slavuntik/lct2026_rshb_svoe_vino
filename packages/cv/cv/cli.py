"""CLI: `cv build-index`, `cv search`, `cv bench`, `cv selfcheck` (бриф п.6)."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

from cv import config, imageio
from cv.augment import VIEWS_DEFAULT, render_synthetic_views, save_synthetic_views
from cv.encoder import SiglipEncoder
from cv.imageio import load_image_file
from cv.index import ImageIndex
from cv.selfcheck import run_selfcheck

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _discover_refs_from_dir(refs_dir: Path) -> dict[str, Path]:
    """slug -> путь к эталонному фото; slug = имя файла без расширения."""
    return {p.stem: p for p in sorted(refs_dir.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS}


def _discover_refs_from_csv(csv_path: Path) -> dict[str, Path]:
    """CSV со столбцами `slug` и `image_path` (или `path`) — как в брифе
    "--refs <dir|csv>"; путь к фото — относительно CSV или абсолютный."""
    out = {}
    base = csv_path.parent
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = row.get("slug")
            raw_path = row.get("image_path") or row.get("path")
            if not slug or not raw_path:
                continue
            p = Path(raw_path)
            out[slug] = p if p.is_absolute() else (base / p)
    return out


def cmd_build_index(args: argparse.Namespace) -> int:
    refs_path = Path(args.refs)
    refs_map = _discover_refs_from_dir(refs_path) if refs_path.is_dir() else _discover_refs_from_csv(refs_path)
    if not refs_map:
        print(f"Не нашёл эталонов в {refs_path}", file=sys.stderr)
        return 1

    cache_dir = Path(args.views_cache) if args.views_cache else config.AUGMENT_CACHE_DIR
    refs: dict[str, list[str]] = {}
    for slug, ref_path in refs_map.items():
        synth_paths = save_synthetic_views(
            ref_path, cache_dir, slug, n=args.views, seed=args.seed, out_size=config.AUGMENT_OUT_SIZE
        )
        refs[slug] = [str(ref_path)] + [str(p) for p in synth_paths]

    index = ImageIndex()
    t0 = time.perf_counter()
    index.build(refs, version=args.version)
    dt = time.perf_counter() - t0
    summary = {"positions": len(refs), "views_per_position": args.views + 1, "version": args.version, "build_s": round(dt, 2)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    image_bytes = Path(args.photo).read_bytes()
    index = ImageIndex()
    t0 = time.perf_counter()
    matches = index.search(image_bytes, top_k=args.top_k, normalize=not args.no_normalize)
    dt_ms = (time.perf_counter() - t0) * 1000
    out = {"timing_ms": round(dt_ms, 1), "matches": [m.__dict__ for m in matches]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    fixtures_dir = Path(args.fixtures)
    paths = sorted(p for p in fixtures_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if args.n:
        paths = paths[: args.n]
    if not paths:
        print(f"Нет фото в {fixtures_dir}", file=sys.stderr)
        return 1
    images = [load_image_file(str(p)) for p in paths]

    report: dict = {"n_images": len(images)}
    for device in [d.strip() for d in args.devices.split(",") if d.strip()]:
        try:
            enc = SiglipEncoder(device=device)
            report[f"embed_{device}"] = enc.benchmark(images, n=args.embed_n)
        except Exception as e:  # noqa: BLE001 — один недоступный девайс не должен ронять весь бенч
            report[f"embed_{device}_error"] = str(e)

    # Свежие ракурсы (не байты самих фикстур) с уникальным seed на кадр — иначе
    # normalize_query(raw fixture bytes) == prepare_reference() из build(), кэш
    # эмбеддингов бьёт 1-в-1 и p95 меряет lookup в dict, а не реальный пайплайн.
    index = ImageIndex()
    fresh_queries = [
        imageio.encode_jpeg(render_synthetic_views(img, n=1, seed=hash(p.name) % 10_000_000)[0])
        for p, img in zip(paths, images)
    ]
    search_timings = []
    for data in fresh_queries:
        t0 = time.perf_counter()
        index.search(data, top_k=5)
        search_timings.append((time.perf_counter() - t0) * 1000)
    if search_timings:
        arr = np.array(search_timings)
        report["search_full"] = {
            "n": len(arr),
            "p50_ms": round(float(np.percentile(arr, 50)), 2),
            "p95_ms": round(float(np.percentile(arr, 95)), 2),
            "max_ms": round(float(arr.max()), 2),
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_selfcheck(args: argparse.Namespace) -> int:
    report = run_selfcheck(Path(args.fixtures), n_views=args.views, seed=args.seed, top_k=args.top_k)
    summary = {k: v for k, v in report.items() if k != "details"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["top1_rate"] >= args.min_rate else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cv", description="CV-ядро сканера «Свой Сомелье»")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build-index", help="Аугментация + эмбеддинг + запись в ImageIndex")
    p_build.add_argument("--refs", required=True, help="Директория эталонов (файл=slug.ext) или CSV (slug,image_path)")
    p_build.add_argument("--version", required=True)
    p_build.add_argument("--views", type=int, default=VIEWS_DEFAULT, help=f"синтетических ракурсов на эталон (default {VIEWS_DEFAULT})")
    p_build.add_argument("--seed", type=int, default=config.AUGMENT_SEED_DEFAULT)
    p_build.add_argument("--views-cache", default=None, help="куда сохранить синтетические ракурсы (default CV_AUGMENT_CACHE_DIR)")
    p_build.set_defaults(func=cmd_build_index)

    p_search = sub.add_parser("search", help="Поиск по фото")
    p_search.add_argument("photo")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--no-normalize", action="store_true", help="A/B: отключить нормализацию запроса")
    p_search.set_defaults(func=cmd_search)

    p_bench = sub.add_parser("bench", help="Тайминги: embed (по девайсам) + полный search")
    p_bench.add_argument("--fixtures", default=str(config.DEVFIX_DIR))
    p_bench.add_argument("--devices", default="mps,cpu")
    p_bench.add_argument("--n", type=int, default=None, help="сколько фото из fixtures использовать (default все)")
    p_bench.add_argument("--embed-n", type=int, default=30, help="сколько замеров embed на устройство")
    p_bench.add_argument("--out", default=None)
    p_bench.set_defaults(func=cmd_bench)

    p_self = sub.add_parser("selfcheck", help="Self-match top-1 свежих ракурсов на построенном индексе")
    p_self.add_argument("--fixtures", default=str(config.DEVFIX_DIR))
    p_self.add_argument("--views", type=int, default=5, help="свежих holdout-ракурсов на фикстуру")
    p_self.add_argument("--seed", type=int, default=config.AUGMENT_SEED_DEFAULT)
    p_self.add_argument("--top-k", type=int, default=5)
    p_self.add_argument("--min-rate", type=float, default=0.9, help="DoD: exit 1, если top1_rate ниже")
    p_self.add_argument("--out", default=None)
    p_self.set_defaults(func=cmd_selfcheck)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
