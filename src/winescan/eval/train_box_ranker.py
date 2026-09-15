"""Обучение выбора рамки по кэшам запросов синтетики.

Запуск: ``python -m winescan.eval.train_box_ranker --caches synth_v1,synth_v2 [--indexes A,B] [--out configs/box_ranker_v1.joblib]``

Метка рамки — IoU с настоящей рамкой ≥ 0,5. Обучение на fold 0 всех кэшей, отчёт на fold 1 каждого:
доля верных рамок, top-1 и top-5 поиска по выбранной рамке против первой рамки по весу.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from winescan.config import PROJECT_ROOT, get_paths
from winescan.eval.offline import QueryCache, folds, top2
from winescan.search.box_ranker import BoxRanker, box_features, train_box_ranker
from winescan.vision.detector import box_iou


def query_rows(cache: QueryCache, query_index: int) -> tuple[list[int], list[list[float]], list[bool]]:
    record = cache.records[query_index]
    slots = [i for i, s in enumerate(record["slots"]) if s["kind"] == "det"]
    gt_slot = cache.slot_of_kind(query_index, "gt")
    if not slots or gt_slot is None:
        return [], [], []
    tops = [top2(cache.scores[query_index, i]) for i in slots]
    rows = box_features(
        [tuple(record["slots"][i]["box"]) for i in slots],
        [record["slots"][i]["det_score"] for i in slots],
        [record["slots"][i]["prior"] for i in slots],
        tuple(record["size"]),
        [t[1] for t in tops],
        [t[2] for t in tops],
    )
    truth = tuple(record["slots"][gt_slot]["box"])
    labels = [box_iou(tuple(record["slots"][i]["box"]), truth) >= 0.5 for i in slots]
    return slots, rows, labels


def evaluate(cache: QueryCache, ids: list[int], ranker: BoxRanker | None) -> dict:
    right, top1, top5 = [], [], []
    for query_index in ids:
        slots, rows, labels = query_rows(cache, query_index)
        expected = cache.expected_index(query_index)
        if not slots or expected is None:
            continue
        choice = ranker.choose(rows) if ranker is not None else 0
        order = np.argsort(-cache.scores[query_index, slots[choice]])[:5]
        right.append(labels[choice])
        top1.append(order[0] == expected)
        top5.append(expected in order)
    return {"queries": len(right), "right_box": float(np.mean(right)), "top1": float(np.mean(top1)), "top5": float(np.mean(top5))}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Обучение выбора рамки")
    parser.add_argument("--caches", default="synth_v1,synth_v2")
    parser.add_argument("--out", default="configs/box_ranker_v1.joblib")
    parser.add_argument("--indexes", default=None, help="другие индексы тех же моделей и видов через запятую")
    args = parser.parse_args(argv)

    indexes = args.indexes.split(",") if args.indexes else None
    caches = {name: QueryCache.load(name, indexes=indexes) for name in args.caches.split(",")}
    rows, labels = [], []
    for cache in caches.values():
        fit, _ = folds(cache)
        for query_index in fit:
            _, query_rows_, query_labels = query_rows(cache, query_index)
            rows += query_rows_
            labels += query_labels
    ranker = BoxRanker(train_box_ranker(rows, labels), meta={"caches": list(caches), "indexes": indexes, "train_boxes": len(rows)})

    report = {}
    for name, cache in caches.items():
        _, check = folds(cache)
        report[name] = {"first_box": evaluate(cache, check, None), "box_ranker": evaluate(cache, check, ranker)}
    ranker.meta["check"] = report
    out = PROJECT_ROOT / args.out
    ranker.save(out)
    (get_paths().artifacts_dir / "cache" / f"box_ranker_report_{out.stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, parts in report.items():
        for variant, metrics in parts.items():
            print(f"{name:10} {variant:11} верная рамка {metrics['right_box']:.3f}  top-1 {metrics['top1']:.3f}  top-5 {metrics['top5']:.3f}")
    print("модель ->", out, f"({out.stat().st_size / 1024:.0f} КБ)")


if __name__ == "__main__":
    main()
