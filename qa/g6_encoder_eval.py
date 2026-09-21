#!/usr/bin/env python3
"""qa/g6_encoder_eval.py — G6: raw top-1/top-5 (БЕЗ OCR/rerank — чистое сравнение
энкодеров) на ТОМ ЖЕ holdout, что использовал G5 (agents/G6-bigger-encoder.md):
case-synth-honest (2054 синтетических фото), сплит `qa/scan_eval.py::assign_split`
(seed=1337, holdout_frac=0.2 по умолчанию — n=374), против ЛЮБОГО уже построенного
ImageIndex (свой CV_DATA_DIR/CV_MODEL — задаются переменными окружения ПЕРЕД запуском,
см. пример ниже, тот же принцип, что qa/accuracy_lab.py).

Срезы (бриф п.3): near-dup (`case-data/families.json`, F3 — `true_slug` состоит в
семье или нет) и fallback-детектора (`packages/cv/data/label_detector_case-20260918.json`
— per-slug исход классического детектора этикетки на ЭТАЛОНЕ; НЕ зависит от энкодера,
переиспользуется как есть для любой модели-кандидата).

Пример:
    cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye
    CV_DATA_DIR=packages/cv/data-exp/siglip2-base-384 \\
    CV_MODEL=google/siglip2-base-patch16-384 \\
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    packages/cv/.venv/bin/python qa/g6_encoder_eval.py --label siglip2-base-384-v8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_CV_PKG_DIR = _REPO_ROOT / "packages" / "cv"
sys.path.insert(0, str(_QA_DIR))
sys.path.insert(0, str(_CV_PKG_DIR))
import scan_eval as se  # noqa: E402 — путь добавлен строкой выше, тот же приём, что accuracy_lab.py

DEFAULT_PHOTOS_DIR = Path(
    "/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/"
    "3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-honest"
)
CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
DEFAULT_FAMILIES_JSON = CASE_DATA_DIR / "families.json"
DEFAULT_LABEL_DETECTOR_JSON = _CV_PKG_DIR / "data" / "label_detector_case-20260918.json"
DEFAULT_OUT_DIR = _QA_DIR / ".cache" / "g6"


def _rate(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "top1": None, "top5": None}
    return {
        "n": n,
        "top1": sum(r["top1_hit"] for r in rows) / n,
        "top5": sum(r["top5_hit"] for r in rows) / n,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos-dir", type=Path, default=DEFAULT_PHOTOS_DIR)
    parser.add_argument("--seed", type=int, default=se.DEFAULT_SEED)
    parser.add_argument("--holdout-frac", type=float, default=se.DEFAULT_HOLDOUT_FRAC)
    parser.add_argument("--families-json", type=Path, default=DEFAULT_FAMILIES_JSON)
    parser.add_argument("--label-detector-json", type=Path, default=DEFAULT_LABEL_DETECTOR_JSON)
    parser.add_argument("--label", required=True, help="метка модели для отчёта/имени выходного файла")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    from cv.families import load_family_by_slug
    from cv.index import ImageIndex

    items, warnings = se.load_eval_set(args.photos_dir)
    for w in warnings:
        print(f"[g6-eval] WARN {w}", file=sys.stderr)
    holdout = [it for it in items if se.assign_split(it.photo_id, args.seed, args.holdout_frac) == "holdout"]
    print(f"[g6-eval] {len(items)} фото всего -> holdout={len(holdout)} (seed={args.seed})", file=sys.stderr)

    family_by_slug = load_family_by_slug(args.families_json)
    label_detector = (
        json.loads(args.label_detector_json.read_text(encoding="utf-8"))
        if args.label_detector_json.is_file()
        else {}
    )
    fallback_slugs = set(label_detector.get("fallback_slugs", []))
    print(
        f"[g6-eval] near-dup семьи: {len(family_by_slug)} слагов охвачено; fallback-слагов: {len(fallback_slugs)}",
        file=sys.stderr,
    )

    index = ImageIndex()
    print(f"[g6-eval] index_version={index.index_version} model={index.encoder.model_name} device={index.encoder.device}", file=sys.stderr)

    rows: list[dict] = []
    t_start = time.perf_counter()
    try:
        for i, it in enumerate(holdout, start=1):
            data = it.path.read_bytes()
            matches = index.search(data, top_k=5, normalize=True)
            top5 = [m.slug for m in matches]
            rows.append(
                {
                    "photo_id": it.photo_id,
                    "true_slug": it.true_slug,
                    "top1_hit": bool(top5 and top5[0] == it.true_slug),
                    "top5_hit": it.true_slug in top5,
                    "near_dup": it.true_slug in family_by_slug,
                    "fallback_ref": it.true_slug in fallback_slugs,
                }
            )
            if i % 50 == 0 or i == len(holdout):
                elapsed = time.perf_counter() - t_start
                rate = i / elapsed if elapsed > 0 else 0.0
                eta = (len(holdout) - i) / rate if rate > 0 else float("inf")
                print(f"[g6-eval] {i}/{len(holdout)} за {elapsed:.0f}с (ETA {eta:.0f}с)", file=sys.stderr)
    finally:
        index.store.close()

    summary = {
        "label": args.label,
        "model": index.encoder.model_name,
        "device": index.encoder.device,
        "index_version": index.index_version,
        "overall": _rate(rows),
        "near_dup": _rate([r for r in rows if r["near_dup"]]),
        "not_near_dup": _rate([r for r in rows if not r["near_dup"]]),
        "fallback_ref": _rate([r for r in rows if r["fallback_ref"]]),
        "clean_ref": _rate([r for r in rows if not r["fallback_ref"]]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    out_path = Path(args.out) if args.out else (DEFAULT_OUT_DIR / f"holdout_{args.label}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[g6-eval] записано: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
