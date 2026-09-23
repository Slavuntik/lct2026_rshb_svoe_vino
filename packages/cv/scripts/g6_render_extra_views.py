#!/usr/bin/env python3
"""packages/cv/scripts/g6_render_extra_views.py — G6 (доп. задание оркестратора,
21.09): рендер ракурсов 25..--upto на usable-слаг для кривой «ракурсы -> top-1»
(25 vs 50), ПОВЕРХ уже закэшированных 1..24 в packages/cv/data/augmented (боевой
кэш, read-only — сюда НЕ пишем).

seed=0 (тот же base seed, что кэш боевых ракурсов) и n=--upto: render_synthetic_
views(image, n, seed=0) тянет параметры СТРОГО по порядку (view 0..n-1) одним
np.random.default_rng(0) — поэтому views[0:24] ПОБАЙТОВО совпадают с уже
закэшированными synth-01..24 (тот же приём, что и в g6_build_index.py: первые N
ракурсов не зависят от того, сколько всего просим), а views[24:upto] — ЧЕСТНОЕ
продолжение ТОЙ ЖЕ детерминированной последовательности (эквивалент "нового сида"
по независимости/непредвзятости, см. отчёт: единый np.random.Generator даёт
качественную псевдослучайную последовательность, 25-й..50-й draw не коррелируют
с 1-м..24-м сильнее, чем при отдельном сиде) — и вдобавок гарантированно БЕЗ
дублей с уже закэшированными видами и БЕЗ рассинхрона: 50-ракурсный набор —
строгий надмножество 25-ракурсного (нужно для чистого сравнения "докинули видов",
не "заменили виды"). Рендерим ВСЕ --upto (включая уже закэшированные 1..24 —
дёшево, классический CV, не энкодер) и сохраняем ТОЛЬКО хвост 25..--upto в
отдельный, вне-боевой каталог.

CPU-only (cv2/numpy), без модели/энкодера — безопасно гонять ПАРАЛЛЕЛЬНО со
сборкой индекса на MPS (разные ресурсы).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CV_PKG_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_CV_PKG_DIR))

DEFAULT_REFS_JSON = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/slug_refs.json")
DEFAULT_UPLOADS_DIR = Path(
    "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"
)
DEFAULT_OUT_DIR = _CV_PKG_DIR / "data-exp" / "augmented-extra"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refs-json", type=Path, default=DEFAULT_REFS_JSON)
    parser.add_argument("--uploads-dir", type=Path, default=DEFAULT_UPLOADS_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--upto", type=int, default=50, help="сколько ракурсов всего (1 real не считая) отрендерить")
    parser.add_argument("--already-cached", type=int, default=24, help="сколько первых уже есть в боевом кэше — не сохраняем повторно")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    from cv.augment import render_synthetic_views
    from cv.cli import discover_refs_from_slug_refs_json
    from cv import imageio

    refs_map = discover_refs_from_slug_refs_json(args.refs_json, args.uploads_dir)
    slugs = sorted(refs_map)
    if args.limit is not None:
        slugs = slugs[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[g6-render-extra] {len(slugs)} слагов, рендер 1..{args.upto} (сохраняем {args.already_cached + 1}..{args.upto})", file=sys.stderr)
    t0 = time.perf_counter()
    n_ok = n_err = 0
    for i, slug in enumerate(slugs, start=1):
        primary = refs_map[slug][0]
        out_paths = [args.out_dir / f"{slug}__synth-{j:02d}.jpg" for j in range(args.already_cached + 1, args.upto + 1)]
        if all(p.is_file() for p in out_paths):
            n_ok += 1
        else:
            try:
                image = imageio.load_image_file(str(primary))
                views = render_synthetic_views(image, n=args.upto, seed=0)  # views[0:already_cached] отброшены (уже в кэше)
                for j, v in enumerate(views[args.already_cached :], start=args.already_cached + 1):
                    (args.out_dir / f"{slug}__synth-{j:02d}.jpg").write_bytes(imageio.encode_jpeg(v))
                n_ok += 1
            except Exception as exc:  # noqa: BLE001 — один битый эталон не должен ронять весь прогон
                n_err += 1
                print(f"[g6-render-extra] WARN {slug}: {exc}", file=sys.stderr)
        if i % 200 == 0 or i == len(slugs):
            elapsed = time.perf_counter() - t0
            rate = i / elapsed if elapsed > 0 else 0.0
            eta = (len(slugs) - i) / rate if rate > 0 else float("inf")
            print(f"[g6-render-extra] {i}/{len(slugs)} за {elapsed:.0f}с (ETA {eta:.0f}с)", file=sys.stderr)

    summary = {"slugs": len(slugs), "ok": n_ok, "errors": n_err, "elapsed_s": round(time.perf_counter() - t0, 1), "out_dir": str(args.out_dir)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
