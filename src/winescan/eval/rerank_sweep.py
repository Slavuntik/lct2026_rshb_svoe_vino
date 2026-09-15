"""Подбор веса текста для переранжирования по сохранённому прогону с OCR.

Запуск: ``python -m winescan.eval.rerank_sweep synth_v1__siglip2-base-patch16-224__gt__ocr``

Берёт predictions.csv (top-10 визуальных кандидатов + текст OCR), для каждого веса
пересчитывает порядок и метрики. Модели не нужны: секунды на весь сплит.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from winescan.config import get_paths
from winescan.eval.run import SUBSETS, summarize
from winescan.search.rerank import rerank
from winescan.search.text_match import LabelText
from winescan.service.pipeline import load_cards

DEFAULT_WEIGHTS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3)


def apply_weight(predictions: pd.DataFrame, cards: dict[str, dict], weight: float) -> pd.DataFrame:
    frame = predictions.copy()
    ranks, margins, scores = [], [], []
    for row in frame.itertuples(index=False):
        slugs = row.top_slugs.split(";")
        visual = [float(v) for v in row.top_scores.split(";")]
        ranked = rerank(slugs, visual, cards, LabelText.from_ocr(row.ocr_text or ""), weight)
        order = [c.slug for c in ranked]
        ranks.append(order.index(row.expected_slug) + 1 if row.expected_slug in order else None)
        scores.append(ranked[0].score)
        margins.append(ranked[0].score - ranked[1].score)
    frame["rank"], frame["margin"], frame["score_top1"] = ranks, margins, scores
    return frame


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Подбор веса текста OCR")
    parser.add_argument("run", help="папка в artifacts/eval")
    parser.add_argument("--weights", type=float, nargs="*", default=list(DEFAULT_WEIGHTS))
    args = parser.parse_args(argv)

    paths = get_paths()
    run_dir = paths.artifacts_dir / "eval" / args.run
    predictions = pd.read_csv(run_dir / "predictions.csv", keep_default_na=False)
    for flag in ("in_phash_group", "shares_image"):
        predictions[flag] = predictions[flag].astype(str).eq("True")
    cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl")

    results = {}
    print("| вес текста | " + " | ".join(f"{p} top-1" for p in SUBSETS) + " | all top-5 | all F1@1 |")
    print("|---" * (len(SUBSETS) + 3) + "|")
    for weight in args.weights:
        metrics = summarize(apply_weight(predictions, cards, weight))
        results[str(weight)] = metrics
        print(f"| {weight} | " + " | ".join(f"{metrics[p]['top1_accuracy']:.3f}" for p in SUBSETS)
              + f" | {metrics['all']['top5_accuracy']:.3f} | {metrics['all']['f1_at_1_best']['f1']:.3f} |")  # fmt: skip
    (run_dir / "rerank_sweep.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
