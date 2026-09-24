#!/usr/bin/env python3
"""qa/gen_case_synthetic_baseline_photos.py — один свежий синтетический ракурс на КАЖДЫЙ
usable-слаг боевого индекса case-20260917 (полномасштабный baseline, поручение
оркестратора в ночь 2026-09-17 поверх agents/F3-census.md).

seed=20260917 (см. SEED) — ОТЛИЧЕН от build-seed (0, packages/cv/cv/config.py::
AUGMENT_SEED_DEFAULT, испечён В индекс) и от seed дельта-селфчека G3 (0+HOLDOUT_SEED_OFFSET
=9973, packages/cv/cv/selfcheck.py) — свежий ракурс, ещё не виденный ни индексом, ни
селфчеком.

ПРАВКА F4 (агент F4, reports/f4-data-hygiene.md, задача 1): раньше `render_synthetic_
views(image, n=1, seed=SEED)` вызывался с ОДНИМ И ТЕМ ЖЕ буквальным SEED на каждый слог
цикла. render_synthetic_views создаёт СВЕЖИЙ np.random.default_rng(seed) на каждый
вызов и тянет ровно n=1 набор параметров ракурса/фона/блика/блюра/шума/экспозиции —
ни один из них не зависит от изображения (см. cv/augment.py::_sample_view_params).
Значит ВСЕ 1982 фото одного прогона получали побайтово ОДИН И ТОТ ЖЕ синтетический
ракурс — отличалась только текстура бутылки. Смена SEED между прогонами поэтому не
сэмплирует типичный разброс сложности по фото, а целиком переносит ВЕСЬ прогон в
другую случайную точку пространства параметров ракурса — это и есть системная причина
аномалии "44,0% (seed 314159, B4) vs 69,4% (seed 20260917, этот прогон)" из ревью 06
п.3 (не шум выборки, воспроизведено статически и эмпирически — direct-embed+cosine,
qa/tests/test_gen_case_synthetic_baseline_photos.py). Починено: каждый слог теперь
получает НЕЗАВИСИМЫЙ, но детерминированный seed = `_stable_seed_for(slug)` (тот же
sha256-приём, что уже был в qa/gen_synthetic_scan_photos.py::_stable_seed_for — эта
правка просто переиспользует существовавший в кодовой базе паттерн). Официальные
baseline-прогоны (reports/f3-synthetic-baseline.md, 69,4%) НЕ перегенерировались —
задокументированы как измерение на одном общем ракурсе, не как ошибка.

Источник эталонов — `case-data/slug_refs.json` через `cv.cli.discover_refs_from_slug_refs_
json` (ТА ЖЕ функция, что использовал G3 при сборке индекса и в scripts/
near_dup_gap_report.py) — уважает `usable:false` автоматически (F3 SigLIP2-триаж), берёт
ПЕРВЫЙ файл на слаг (эталон = "real"-вью).

Запускать через venv пакета cv (нужны cv2/numpy/PIL):
    packages/cv/.venv/bin/python qa/gen_case_synthetic_baseline_photos.py \\
        --out-dir <scratchpad>/case-synthetic-baseline [--limit N]

Имя файла — `<slug>__synth.jpg` (конвенция qa/scan_eval.py::infer_slug_from_filename,
разделитель `__` — слаги сами его не содержат) — CSV-разметка не нужна, label по имени.

`--limit N` (< числа usable-слагов) включает СТРАТИФИЦИРОВАННУЮ выборку: все слаги из
near-dup семей (`case-data/families.json`) — ОБЯЗАТЕЛЬНО, добивка случайными прочими до N
(детерминированно по SEED) — ровно как просил оркестратор на случай нехватки времени.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_DIR = CASE_DATA_DIR / "prod-svoe-vino-strapi" / "prod-svoe-vino" / "strapi" / "uploads"
SLUG_REFS_PATH = CASE_DATA_DIR / "slug_refs.json"
FAMILIES_PATH = CASE_DATA_DIR / "families.json"

SEED = 20260917


def _stable_seed_for(slug: str, base_seed: int = SEED) -> int:
    """Детерминированный per-slug seed, производный от `base_seed` — тот же приём, что
    `qa/gen_synthetic_scan_photos.py::_stable_seed_for` (sha256, НЕ встроенный `hash()`,
    рандомизированный между процессами без PYTHONHASHSEED). Чинит системную аномалию
    (см. докстринг модуля, "ПРАВКА F4"): каждый слог теперь рендерится на СВОЁМ ракурсе,
    а не на одном общем для всего прогона."""
    digest = hashlib.sha256(f"{base_seed}:{slug}".encode("utf-8")).hexdigest()
    return base_seed + (int(digest[:8], 16) % 1_000_000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="< числа usable-слагов -> стратифицированная выборка")
    parser.add_argument("--slug-refs", type=Path, default=SLUG_REFS_PATH)
    parser.add_argument("--uploads-dir", type=Path, default=UPLOADS_DIR)
    parser.add_argument("--families-json", type=Path, default=FAMILIES_PATH)
    args = parser.parse_args(argv)

    from cv import imageio
    from cv.augment import render_synthetic_views
    from cv.cli import discover_refs_from_slug_refs_json

    refs_map = discover_refs_from_slug_refs_json(args.slug_refs, args.uploads_dir)
    slugs = sorted(refs_map)
    print(f"[gen] {len(slugs)} usable слагов из {args.slug_refs}", file=sys.stderr)

    mandatory_count = 0
    if args.limit is not None and args.limit < len(slugs):
        family_slugs: set[str] = set()
        if args.families_json.is_file():
            fam = json.loads(args.families_json.read_text(encoding="utf-8"))
            for f in fam.values():
                family_slugs.update(f.get("slugs", []))
        mandatory = sorted(s for s in slugs if s in family_slugs)
        rest = sorted(s for s in slugs if s not in family_slugs)
        rng = random.Random(SEED)
        rng.shuffle(rest)
        take_rest = max(0, args.limit - len(mandatory))
        slugs = sorted(set(mandatory) | set(rest[:take_rest]))
        mandatory_count = len(mandatory)
        print(
            f"[gen] стратифицированная выборка: {mandatory_count} near-dup (обязательно) + "
            f"{len(slugs) - mandatory_count} прочих = {len(slugs)}",
            file=sys.stderr,
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    n_ok = 0
    errors: list[dict[str, str]] = []
    for i, slug in enumerate(slugs, start=1):
        path = refs_map[slug][0]  # первый файл = эталон
        try:
            image = imageio.load_image_file(str(path))
            views = render_synthetic_views(image, n=1, seed=_stable_seed_for(slug))
            out_path = args.out_dir / f"{slug}__synth.jpg"
            out_path.write_bytes(imageio.encode_jpeg(views[0]))
            n_ok += 1
        except Exception as exc:  # noqa: BLE001 — один битый эталон не должен ронять весь прогон
            errors.append({"slug": slug, "error": str(exc)})
            print(f"[gen] WARN {slug}: {exc}", file=sys.stderr)
        if i % 200 == 0:
            elapsed = time.perf_counter() - t0
            print(f"[gen] {i}/{len(slugs)}, {elapsed:.1f}с ({elapsed/i*1000:.0f}мс/фото)", file=sys.stderr)

    elapsed = time.perf_counter() - t0
    summary = {
        "seed": SEED,
        "seed_scheme": "stable_per_slug (sha256(seed:slug), см. _stable_seed_for — F4-фикс)",
        "requested_slugs": len(slugs),
        "generated": n_ok,
        "errors": len(errors),
        "mandatory_near_dup_included": mandatory_count,
        "elapsed_s": round(elapsed, 1),
        "out_dir": str(args.out_dir),
    }
    (args.out_dir / "_generation_summary.json").write_text(
        json.dumps({**summary, "error_detail": errors}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
