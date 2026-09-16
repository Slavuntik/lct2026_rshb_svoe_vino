"""CLI: `cv build-index`, `cv search`, `cv bench`, `cv selfcheck` (бриф п.6)."""
from __future__ import annotations

import argparse
import csv
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

from cv import config, imageio
from cv.audit import label_detector_outcomes
from cv.augment import VIEWS_DEFAULT, render_synthetic_views, save_synthetic_views
from cv.encoder import SiglipEncoder
from cv.imageio import load_image_file
from cv.index import ImageIndex
from cv.selfcheck import run_selfcheck, run_selfcheck_from_refs

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


def discover_refs_from_slug_refs_json(refs_json: Path, uploads_dir: Path) -> dict[str, list[Path]]:
    """slug -> [эталон, доп. реальные ракурсы...] из `case-data/slug_refs.json`
    (agents/G3-real-index.md п.1). Первый файл слага = эталон; остальные (если есть)
    добавляются доп-ракурсами (`ImageIndex.build()` даёт им view="real-2.." по имени
    файла, не по позиции — см. cv/index.py).

    Схема датасета менялась ПРЯМО В ПРОЦЕССЕ этой волны (F3, `qa/case_census.py`,
    16.09.2026): было `files: list[str]`; стало `chosen: str` (F3 сам разрешил
    неоднозначность между кандидатами) + `candidates: list[str]` (что рассматривалось)
    + `usable: bool` (семантический триаж SigLIP2 zero-shot — лайфстайл/виноградник/
    интерьер вместо бутылки, см. `ref_quality_meta` в самом JSON). Поддерживаем ОБЕ
    схемы: `files`, если есть (старые фикстуры/тесты), иначе `chosen`+`candidates`.

    `usable is False` — исключаем: пересборка без шумных эталонов становится той же
    командой с обновлённым JSON (дёшево через кэш эмбеддингов — исключённые слаги
    просто не попадают в refs, остальные бьют в `.embed_cache` как раньше). Слаги без
    кандидатов вовсе (`no_ref_slugs` в JSON) пропускаются молча — ожидаемое состояние
    датасета в процессе дозаполнения, не ошибка."""
    payload = json.loads(refs_json.read_text(encoding="utf-8"))
    mapping = payload.get("mapping", payload)  # поддержка и {"mapping": {...}}, и плоского {slug: {...}}

    out: dict[str, list[Path]] = {}
    n_missing_on_disk = 0
    n_unusable = 0
    for slug, info in mapping.items():
        if info.get("usable") is False:
            n_unusable += 1
            continue
        if "files" in info:  # старая схема (до миграции F3 на chosen/candidates)
            filenames = list(info.get("files") or [])
        else:
            chosen = info.get("chosen")
            candidates = [c for c in (info.get("candidates") or []) if c != chosen]
            filenames = ([chosen] if chosen else []) + candidates
        paths = []
        for fname in filenames:
            p = uploads_dir / fname
            if p.is_file():
                paths.append(p)
            else:
                n_missing_on_disk += 1
        if paths:
            out[slug] = paths
    if n_missing_on_disk:
        print(
            f"[build-index] {n_missing_on_disk} файлов из {refs_json.name} не найдено в {uploads_dir} — пропущены",
            file=sys.stderr,
        )
    if n_unusable:
        print(f"[build-index] {n_unusable} слагов помечены usable=false — исключены", file=sys.stderr)
    return out


def load_near_dup_groups(families_json: Path) -> list[frozenset[str]]:
    """Реальные near-dup семьи каталога как frozenset на семью — для
    `run_selfcheck_from_refs(near_dup_groups=...)` на боевых данных. Дев-фикстурная
    `cv.selfcheck.NEAR_DUP_GROUPS` покрывает только ОДНУ известную пару
    (`aligote-barrel-2024/2025`), не полный список семей каталога — без подмены
    self-match несправедливо штрафовал бы остальные семьи за попадание top-1 в
    соседа по серии (contracts/image-scan.md: разделять их — работа OCR-
    верификатора, не CV).

    F3 (`qa/case_census.py`) ПЕРЕЕХАЛ с семей, встроенных в `slug_refs.json["families"]`
    (плоский `{family: [slug, ...]}`), на отдельный файл `case-data/families.json`
    (`{family_id: {"slugs": [...], "chosen_files": {...}, "ocr": {...}, ...}}`,
    16.09.2026, в процессе этой волны) — поддерживаем ОБЕ формы значений (голый список
    ИЛИ dict с ключом "slugs") и обе обёртки (отдельный файл ИЛИ встроенный ключ
    "families"), чтобы не переломиться при следующей миграции формата. Отсутствующий
    файл -> пустой список групп (не ошибка — вызывающий код тогда просто не даёт
    поблажки near-dup соседям, консервативное поведение)."""
    if not families_json.exists():
        return []
    payload = json.loads(families_json.read_text(encoding="utf-8"))
    families = payload.get("families", payload)  # {"families": {...}} (старое) или плоский (новое)
    groups = []
    for v in families.values():
        slugs = v.get("slugs") if isinstance(v, dict) else v  # новая схема F3 (dict) vs старая (list)
        if slugs:
            groups.append(frozenset(slugs))
    return groups


def cmd_build_index(args: argparse.Namespace) -> int:
    if args.refs_json:
        if not args.uploads_dir:
            print("--refs-json требует --uploads-dir", file=sys.stderr)
            return 1
        refs_map = discover_refs_from_slug_refs_json(Path(args.refs_json), Path(args.uploads_dir))
    elif args.refs:
        refs_path = Path(args.refs)
        flat = _discover_refs_from_dir(refs_path) if refs_path.is_dir() else _discover_refs_from_csv(refs_path)
        refs_map = {slug: [p] for slug, p in flat.items()}
    else:
        print("нужен либо --refs <dir|csv>, либо --refs-json ВМЕСТЕ с --uploads-dir", file=sys.stderr)
        return 1

    if not refs_map:
        print("Не нашёл эталонов", file=sys.stderr)
        return 1

    # Дополнение оркестратора (16.09.2026, п.1): по каждому эталону — исход детектора
    # этикетки (нашёл силуэт бутылки или откатился на fallback-кроп всего кадра) —
    # дешёвая классическая CV, отдельно от долгого embed. primary = paths[0] (эталон,
    # не доп-ракурсы и не синтетика — см. discover_refs_from_slug_refs_json).
    audit = label_detector_outcomes({slug: paths[0] for slug, paths in refs_map.items()})
    print(
        f"[build-index] детектор этикетки: fallback {audit['fallback_count']}/{audit['total']} "
        f"({audit['fallback_rate']:.1%})",
        file=sys.stderr,
    )
    audit_path = Path(args.audit_out) if args.audit_out else (config.DATA_DIR / f"label_detector_{args.version}.json")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    cache_dir = Path(args.views_cache) if args.views_cache else config.AUGMENT_CACHE_DIR
    refs: dict[str, list[str]] = {}
    t_augment_start = time.perf_counter()
    n_extra_real = 0
    for i, (slug, ref_paths) in enumerate(refs_map.items(), start=1):
        primary, *extra_real = ref_paths
        n_extra_real += len(extra_real)
        synth_paths = save_synthetic_views(
            primary, cache_dir, slug, n=args.views, seed=args.seed, out_size=config.AUGMENT_OUT_SIZE
        )
        refs[slug] = [str(primary)] + [str(p) for p in extra_real] + [str(p) for p in synth_paths]
        if i % 200 == 0:
            elapsed = time.perf_counter() - t_augment_start
            print(f"[build-index] augment {i}/{len(refs_map)} слагов, {elapsed:.0f} с", file=sys.stderr)
    augment_s = time.perf_counter() - t_augment_start

    index = ImageIndex()
    t0 = time.perf_counter()
    index.build(refs, version=args.version)
    dt = time.perf_counter() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)  # macOS: ru_maxrss в байтах
    summary = {
        "positions": len(refs),
        "views_per_position": args.views + 1,
        "extra_real_angles": n_extra_real,
        "total_vectors": sum(len(v) for v in refs.values()),
        "version": args.version,
        "build_s": round(dt, 2),
        "augment_s": round(augment_s, 2),
        "stages": index.last_build_stats,
        "peak_rss_mb": round(peak_rss_mb, 1),
        "label_detector": {
            "total": audit["total"],
            "fallback_count": audit["fallback_count"],
            "fallback_rate": audit["fallback_rate"],
            "fallback_slugs_file": str(audit_path),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_audit_refs(args: argparse.Namespace) -> int:
    """Отдельный проход детектора этикетки по эталонам — БЕЗ энкодера/Qdrant (см.
    cv/audit.py). Для случая "сборка уже прошла точку — добери отдельным проходом"
    (дополнение оркестратора, agents/G3-real-index.md): не требует пересборки индекса,
    читает те же файлы-эталоны из --refs-json/--uploads-dir ИЛИ --fixtures."""
    if args.refs_json:
        if not args.uploads_dir:
            print("--refs-json требует --uploads-dir", file=sys.stderr)
            return 1
        refs_map = discover_refs_from_slug_refs_json(Path(args.refs_json), Path(args.uploads_dir))
        primary_refs = {slug: paths[0] for slug, paths in refs_map.items()}
    else:
        fixtures_dir = Path(args.fixtures)
        primary_refs = {p.stem: p for p in sorted(fixtures_dir.iterdir()) if p.suffix.lower() in IMAGE_EXTS}

    report = label_detector_outcomes(primary_refs)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("per_slug",)}
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
    if args.refs_json:
        if not args.uploads_dir:
            print("--refs-json требует --uploads-dir", file=sys.stderr)
            return 1
        refs_map = discover_refs_from_slug_refs_json(Path(args.refs_json), Path(args.uploads_dir))
        paths = sorted(paths[0] for paths in refs_map.values())  # эталон (первый файл) каждого слага
        if args.n and args.n < len(paths):
            # Случайная выборка по каталогу (не первые N алфавитно — иначе смещение
            # к одной винодельне/паттерну имён), детерминирована по --sample-seed.
            rng = np.random.default_rng(args.sample_seed)
            idx = rng.choice(len(paths), size=args.n, replace=False)
            paths = sorted(paths[i] for i in idx)
    else:
        fixtures_dir = Path(args.fixtures)
        paths = sorted(p for p in fixtures_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if args.n:
            paths = paths[: args.n]
    if not paths:
        print("Нет фото", file=sys.stderr)
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
    if args.refs_json:
        if not args.uploads_dir:
            print("--refs-json требует --uploads-dir", file=sys.stderr)
            return 1
        refs_map = discover_refs_from_slug_refs_json(Path(args.refs_json), Path(args.uploads_dir))
        primary_refs = {slug: paths[0] for slug, paths in refs_map.items()}  # эталон = первый файл
        # Дополнение оркестратора п.3: помечаем self-match отдельной строкой по
        # позициям с fallback-детектором — тот же дешёвый классический проход, что
        # `cv audit-refs`/`cmd_build_index` (не энкодер, секунды даже на всём датасете).
        audit = label_detector_outcomes(primary_refs, verbose=False)
        families_json = Path(args.families_json) if args.families_json else Path(args.refs_json).parent / "families.json"
        report = run_selfcheck_from_refs(
            primary_refs,
            n_views=args.views,
            seed=args.seed,
            top_k=args.top_k,
            sample_n=args.sample_n,
            sample_seed=args.sample_seed,
            near_dup_groups=load_near_dup_groups(families_json),
            fallback_slugs=set(audit["fallback_slugs"]),
        )
    else:
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
    p_build.add_argument("--refs", default=None, help="Директория эталонов (файл=slug.ext) или CSV (slug,image_path)")
    p_build.add_argument("--refs-json", default=None, help="case-data/slug_refs.json (mapping[slug]={..., files[]}) — вместе с --uploads-dir")
    p_build.add_argument("--uploads-dir", default=None, help="директория с файлами files[] из --refs-json")
    p_build.add_argument("--version", required=True)
    p_build.add_argument("--views", type=int, default=VIEWS_DEFAULT, help=f"синтетических ракурсов на эталон (default {VIEWS_DEFAULT})")
    p_build.add_argument("--seed", type=int, default=config.AUGMENT_SEED_DEFAULT)
    p_build.add_argument("--views-cache", default=None, help="куда сохранить синтетические ракурсы (default CV_AUGMENT_CACHE_DIR)")
    p_build.add_argument("--out", default=None, help="куда сохранить JSON-сводку сборки")
    p_build.add_argument("--audit-out", default=None, help="куда сохранить полный отчёт детектора этикетки (default CV_DATA_DIR/label_detector_<version>.json)")
    p_build.set_defaults(func=cmd_build_index)

    p_audit = sub.add_parser("audit-refs", help="Только детектор этикетки по эталонам (без энкодера/Qdrant) — отдельный дешёвый проход")
    p_audit.add_argument("--fixtures", default=str(config.DEVFIX_DIR), help="дев-директория (slug=имя файла); игнорируется, если задан --refs-json")
    p_audit.add_argument("--refs-json", default=None)
    p_audit.add_argument("--uploads-dir", default=None)
    p_audit.add_argument("--out", default=None, help="куда сохранить полный JSON (per_slug + fallback_slugs)")
    p_audit.set_defaults(func=cmd_audit_refs)

    p_search = sub.add_parser("search", help="Поиск по фото")
    p_search.add_argument("photo")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--no-normalize", action="store_true", help="A/B: отключить нормализацию запроса")
    p_search.set_defaults(func=cmd_search)

    p_bench = sub.add_parser("bench", help="Тайминги: embed (по девайсам) + полный search")
    p_bench.add_argument("--fixtures", default=str(config.DEVFIX_DIR), help="дев-директория; игнорируется, если задан --refs-json")
    p_bench.add_argument("--refs-json", default=None, help="case-data/slug_refs.json — бенч на боевых эталонах, вместе с --uploads-dir")
    p_bench.add_argument("--uploads-dir", default=None)
    p_bench.add_argument("--sample-seed", type=int, default=0, help="для случайной выборки --n эталонов из --refs-json")
    p_bench.add_argument("--devices", default="mps,cpu")
    p_bench.add_argument("--n", type=int, default=None, help="сколько фото использовать (default все)")
    p_bench.add_argument("--embed-n", type=int, default=30, help="сколько замеров embed на устройство")
    p_bench.add_argument("--out", default=None)
    p_bench.set_defaults(func=cmd_bench)

    p_self = sub.add_parser("selfcheck", help="Self-match top-1/top-5 свежих ракурсов на построенном индексе")
    p_self.add_argument("--fixtures", default=str(config.DEVFIX_DIR), help="дев-директория (slug=имя файла); игнорируется, если задан --refs-json")
    p_self.add_argument("--refs-json", default=None, help="case-data/slug_refs.json — селфчек на боевых эталонах, вместе с --uploads-dir")
    p_self.add_argument("--uploads-dir", default=None, help="директория с файлами files[] из --refs-json")
    p_self.add_argument("--families-json", default=None, help="near-dup семьи (default: families.json рядом с --refs-json)")
    p_self.add_argument("--sample-n", type=int, default=None, help="сколько слагов сэмплировать из --refs-json (default: все)")
    p_self.add_argument("--sample-seed", type=int, default=0)
    p_self.add_argument("--views", type=int, default=5, help="свежих holdout-ракурсов на фикстуру/слаг")
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
