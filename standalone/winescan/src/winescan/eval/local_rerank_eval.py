"""Переранжирование сохранённого прогона по локальным признакам и подбор веса.

Запуск::

    python -m winescan.eval.local_rerank_eval <run> --split synth_v1 [--top 5] [--workers 32]

Для каждого запроса берёт кроп по сохранённой рамке (``box`` в predictions.csv), считает
SIFT-совпадения с эталонами top-K кандидатов (CPU, параллельно), сохраняет их в
local_inliers.csv и печатает метрики для разных весов: скор = визуальный + вес × бонус.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
from PIL import Image

from winescan.config import get_paths
from winescan.eval.run import SUBSETS, _load_query, load_split, summarize
from winescan.search.local_match import extract, inliers
from winescan.search.rerank import fuse
from winescan.search.text_match import LabelText, text_score
from winescan.service.pipeline import load_cards
from winescan.vision.preprocess import crop_box, cutout

DEFAULT_WEIGHTS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2)


@lru_cache(maxsize=4096)
def _reference_features(image_file: str):
    with Image.open(get_paths().uploads_dir / image_file) as image:
        image.load()
        image = image.copy()
    image.thumbnail((1024, 1024))
    return extract(cutout(image).convert("RGB"))


_image_of: dict[str, str] = {}


def _init(image_of: dict[str, str]) -> None:
    _image_of.update(image_of)


def _count(task: tuple[str, str, list[str]]) -> list[int]:
    image_path, box_text, slugs = task
    image, _ = _load_query(image_path)
    box = tuple(float(v) for v in box_text.split(",")) if box_text else None
    query = extract(crop_box(image, box) if box else image)
    return [inliers(query, _reference_features(_image_of[slug])) if slug in _image_of else 0 for slug in slugs]


def apply_weight(
    predictions: pd.DataFrame, weight: float, cards: dict[str, dict] | None = None, text_weight: float = 0.0
) -> pd.DataFrame:
    """Переранжирование top-K тем же слиянием, что в сервисе: визуальный + SIFT (+ текст OCR)."""
    frame = predictions.copy()
    ranks, margins, scores = [], [], []
    for row in frame.itertuples(index=False):
        slugs = row.top_slugs.split(";")[: len(row.inliers)]
        visual = [float(v) for v in row.top_scores.split(";")][: len(slugs)]
        texts = None
        if text_weight and cards is not None:
            label = LabelText.from_ocr(getattr(row, "ocr_text", "") or "")
            texts = {s: text_score(cards[s], label) for s in slugs if s in cards}
        ranked = fuse(slugs, visual, dict(zip(slugs, row.inliers)), texts, weight, text_weight)
        order = [c.slug for c in ranked]
        ranks.append(order.index(row.expected_slug) + 1 if row.expected_slug in order else None)
        scores.append(ranked[0].score)
        margins.append(ranked[0].score - ranked[1].score)
    frame["rank"], frame["margin"], frame["score_top1"] = ranks, margins, scores
    return frame


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SIFT-переранжирование сохранённого прогона")
    parser.add_argument("run", help="папка в artifacts/eval")
    parser.add_argument("--split", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--weights", type=float, nargs="*", default=list(DEFAULT_WEIGHTS))
    parser.add_argument("--text-weight", type=float, default=0.0, help="добавить текст OCR (нужен прогон с --ocr)")
    args = parser.parse_args(argv)

    paths = get_paths()
    run_dir = paths.artifacts_dir / "eval" / args.run
    predictions = pd.read_csv(run_dir / "predictions.csv", keep_default_na=False)
    for flag in ("in_phash_group", "shares_image"):
        predictions[flag] = predictions[flag].astype(str).eq("True")
    manifest, images_dir = load_split(args.split)
    image_path_of = dict(zip(manifest["query_id"], manifest["image_path"]))
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    image_of = dict(zip(catalog["slug"], catalog["image_file"]))

    cache = run_dir / f"local_inliers_top{args.top}.csv"
    if cache.exists():
        counts = pd.read_csv(cache, keep_default_na=False)
        inlier_lists = dict(zip(counts["query_id"], counts["inliers"].map(lambda s: [int(v) for v in s.split(";")])))
    else:
        tasks = [
            (str(images_dir / image_path_of[row.query_id]), row.box, row.top_slugs.split(";")[: args.top])
            for row in predictions.itertuples(index=False)
        ]
        workers = args.workers or min(32, os.cpu_count() or 1)
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(image_of,)) as pool:
            results = list(pool.map(_count, tasks, chunksize=8))
        inlier_lists = dict(zip(predictions["query_id"], results))
        pd.DataFrame(
            {"query_id": list(inlier_lists), "inliers": [";".join(map(str, v)) for v in inlier_lists.values()]}
        ).to_csv(cache, index=False)

    predictions["inliers"] = predictions["query_id"].map(inlier_lists)
    cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl") if args.text_weight else None
    results = {}
    print(f"вес текста OCR: {args.text_weight}")
    print("| вес SIFT | " + " | ".join(f"{p} top-1" for p in SUBSETS) + " | all top-5 | all F1@1 |")
    print("|---" * (len(SUBSETS) + 3) + "|")
    for weight in args.weights:
        metrics = summarize(apply_weight(predictions, weight, cards, args.text_weight))
        results[str(weight)] = metrics
        print(f"| {weight} | " + " | ".join(f"{metrics[p]['top1_accuracy']:.3f}" for p in SUBSETS)
              + f" | {metrics['all']['top5_accuracy']:.3f} | {metrics['all']['f1_at_1_best']['f1']:.3f} |")  # fmt: skip
    expected_rank1 = np.mean([row.inliers[0] for row in predictions.itertuples() if row.inliers])
    print(f"среднее число inliers у визуального top-1: {expected_rank1:.1f}")
    suffix = f"_text{args.text_weight:g}" if args.text_weight else ""
    (run_dir / f"local_rerank_sweep_top{args.top}{suffix}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
