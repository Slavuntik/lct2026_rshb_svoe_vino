#!/usr/bin/env python3
"""packages/cv/scripts/g6_build_index.py — G6: собрать ЭКСПЕРИМЕНТАЛЬНЫЙ ImageIndex для
кандидата SigLIP 2 (agents/G6-bigger-encoder.md), БЕЗ обучения, в отдельном CV_DATA_DIR —
боевой `packages/cv/data` не трогается (жёсткая проверка ниже).

Ракурсы НЕ рендерятся заново: для каждого слага берём эталон (первый файл из
`case-data/slug_refs.json`) + первые `--views` уже отрендеренных `{slug}__synth-NN.jpg`
из `packages/cv/data/augmented` (боевой кэш аугментации G3, READ-ONLY reuse — бриф п.2:
"считать заново только эмбеддинги"). `render_synthetic_views(image, n, seed=0)` тянет
параметры ракурсов СТРОГО по порядку — первые V файлов при любом V теми же байтами, что
и в полном 24-ракурсном наборе (cv/augment.py), так что урезание views не меняет САМИ
ракурсы, только их число.

Два режима:
  --probe N   — ТОЛЬКО embed-бенч (mps+cpu) на N сэмплах реальных эталонов, БЕЗ сборки
                индекса (первый вызов на новой модели заодно качает веса с HF — бриф
                разрешил сеть для загрузки). Для выбора --views по бюджету времени.
  --views V --data-dir DIR --version NAME — полная сборка (все usable-слаги, если не
                указан --limit).

Пример:
    cd packages/cv && source .venv/bin/activate
    python3 scripts/g6_build_index.py --model google/siglip2-base-patch16-384 --probe 16
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/g6_build_index.py \\
        --model google/siglip2-base-patch16-384 --views 8 \\
        --data-dir data-exp/siglip2-base-384 --version g6-siglip2-base-384
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CV_PKG_DIR = _SCRIPTS_DIR.parent  # packages/cv
sys.path.insert(0, str(_CV_PKG_DIR))

DEFAULT_REFS_JSON = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/slug_refs.json")
DEFAULT_UPLOADS_DIR = Path(
    "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"
)
DEFAULT_AUGMENTED_DIR = _CV_PKG_DIR / "data" / "augmented"  # боевой кэш аугментации, READ-ONLY
COMBAT_DATA_DIR = (_CV_PKG_DIR / "data").resolve()  # запрещённая цель для --data-dir


def _load_refs_map(refs_json: Path, uploads_dir: Path) -> dict[str, list[Path]]:
    from cv.cli import discover_refs_from_slug_refs_json

    return discover_refs_from_slug_refs_json(refs_json, uploads_dir)


def build_refs(
    refs_json: Path, uploads_dir: Path, augmented_dir: Path, views: int, limit: int | None
) -> dict[str, list[str]]:
    refs_map = _load_refs_map(refs_json, uploads_dir)
    slugs = sorted(refs_map)
    if limit is not None:
        slugs = slugs[:limit]

    refs: dict[str, list[str]] = {}
    missing_views = 0
    for slug in slugs:
        primary, *extra_real = refs_map[slug]
        synth_paths = [augmented_dir / f"{slug}__synth-{i:02d}.jpg" for i in range(1, views + 1)]
        present = [p for p in synth_paths if p.is_file()]
        missing_views += views - len(present)
        refs[slug] = [str(primary)] + [str(p) for p in extra_real] + [str(p) for p in present]
    if missing_views:
        print(
            f"[g6-build] ВНИМАНИЕ: {missing_views} synth-ракурсов не найдено в {augmented_dir} "
            f"(views-кэш рассчитан на <= {views}?)",
            file=sys.stderr,
        )
    return refs


def cmd_probe(args: argparse.Namespace) -> int:
    from cv.encoder import SiglipEncoder
    from cv.imageio import load_image_file

    refs_map = _load_refs_map(args.refs_json, args.uploads_dir)
    slugs = sorted(refs_map)[: args.probe]
    images = [load_image_file(str(refs_map[s][0])) for s in slugs]
    print(f"[g6-probe] {args.model}: {len(images)} сэмплов эталонов", file=sys.stderr)

    out: dict = {"model": args.model, "n_sample": len(images)}
    for device in ["mps", "cpu"]:
        t0 = time.perf_counter()
        try:
            enc = SiglipEncoder(model_name=args.model, device=device)
            bench = enc.benchmark(images, n=len(images))
            out[device] = bench
            print(
                f"[g6-probe] {device}: p50={bench['p50_ms']}мс p95={bench['p95_ms']}мс "
                f"mean={bench['mean_ms']}мс (загрузка+прогон {time.perf_counter() - t0:.1f}с)",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — недоступный девайс не должен ронять весь probe
            out[f"{device}_error"] = str(exc)
            print(f"[g6-probe] {device}: ОШИБКА {exc}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).resolve()
    if data_dir == COMBAT_DATA_DIR:
        print(f"[g6-build] ОТКАЗ: --data-dir совпадает с боевым {COMBAT_DATA_DIR} — запрещено брифом", file=sys.stderr)
        return 1
    # Резолвится в момент импорта cv.config ниже — поэтому выставляем ДО импорта.
    os.environ["CV_MODEL"] = args.model
    os.environ["CV_DATA_DIR"] = str(data_dir)

    from cv.index import ImageIndex

    refs = build_refs(args.refs_json, args.uploads_dir, args.augmented_dir, args.views, args.limit)
    n_vectors = sum(len(v) for v in refs.values())
    print(
        f"[g6-build] model={args.model} data_dir={data_dir} positions={len(refs)} "
        f"views/position={args.views + 1} vectors~={n_vectors}",
        file=sys.stderr,
    )

    index = ImageIndex()
    t0 = time.perf_counter()
    try:
        index.build(refs, version=args.version)
    finally:
        index.store.close()  # лок embedded-qdrant этого CV_DATA_DIR освобождён для следующего прогона
    dt = time.perf_counter() - t0

    qdrant_dir = data_dir / "qdrant"
    du_bytes = sum(f.stat().st_size for f in qdrant_dir.rglob("*") if f.is_file()) if qdrant_dir.is_dir() else 0

    summary = {
        "model": args.model,
        "data_dir": str(data_dir),
        "version": args.version,
        "positions": len(refs),
        "views_per_position": args.views + 1,
        "total_vectors": n_vectors,
        "build_s": round(dt, 2),
        "stages": index.last_build_stats,
        "qdrant_bytes": du_bytes,
        "qdrant_mb": round(du_bytes / (1024 * 1024), 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="HF checkpoint, напр. google/siglip2-large-patch16-256")
    parser.add_argument("--refs-json", type=Path, default=DEFAULT_REFS_JSON)
    parser.add_argument("--uploads-dir", type=Path, default=DEFAULT_UPLOADS_DIR)
    parser.add_argument("--augmented-dir", type=Path, default=DEFAULT_AUGMENTED_DIR)
    parser.add_argument("--views", type=int, default=8, help="synth-ракурсов на позицию (из кэша, без рендера)")
    parser.add_argument("--limit", type=int, default=None, help="ограничить число слагов (смоук)")
    parser.add_argument("--data-dir", default=None, help="CV_DATA_DIR для ЭТОГО индекса (не packages/cv/data)")
    parser.add_argument("--version", default=None)
    parser.add_argument("--probe", type=int, default=None, help="если задан — только embed-бенч на N сэмплах")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    if args.probe is not None:
        return cmd_probe(args)
    if not args.data_dir or not args.version:
        parser.error("--data-dir и --version обязательны для сборки (для --probe не нужны)")
    return cmd_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
