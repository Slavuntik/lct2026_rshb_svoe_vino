"""Explain positive-label misses from saved diagnostics, never during inference."""

import argparse
from collections import Counter
import json
from pathlib import Path
from evaluate_shelves import iou


def analyze(dataset, results):
    rows = {r["file"]: r for r in results["rows"]}
    limit = results["config"]["rescueLimit"]
    details = []
    for photo in dataset["photos"]:
        if photo["file"] not in rows:
            continue
        observations = rows[photo["file"]].get("observations", [])
        for region in photo["regions"]:
            expected = region.get("productId")
            if not expected:
                continue
            item = max(
                observations, key=lambda o: iou(o["box"], region["box"]), default=None
            )
            if item is None or iou(item["box"], region["box"]) < 0.5:
                reason = "no_matching_detection"
                item = {}
            elif item.get("id") == expected:
                reason = "accepted"
            elif item.get("id"):
                reason = "accepted_other_product"
            elif item.get("tooSmall"):
                reason = "too_small"
            elif "rescueQueueRank" not in item:
                reason = "not_queued"
            elif item["rescueQueueRank"] > limit:
                reason = "outside_work_limit"
            elif "rescueCandidates" not in item:
                reason = "skipped_before_strong_match"
            elif expected not in item["rescueCandidates"]:
                reason = "missing_from_strong_candidates"
            else:
                reason = "checked_but_rejected"
            details.append(
                {
                    "file": photo["file"],
                    "region": region["number"],
                    "expected": expected,
                    "reason": reason,
                    "queueRank": item.get("rescueQueueRank"),
                    "priority": item.get("rescuePriority"),
                }
            )
    return {
        "independentGroundTruth": False,
        "config": results["config"],
        "photos": len(rows),
        "missingPhotos": [
            p["file"] for p in dataset["photos"] if p["file"] not in rows
        ],
        "counts": dict(Counter(r["reason"] for r in details)),
        "regions": details,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/shelves-v1.json")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(
        json.loads(args.dataset.read_text()), json.loads(args.results.read_text())
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
