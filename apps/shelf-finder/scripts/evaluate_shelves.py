"""Score frozen partial shelf annotations; unresolved regions never count as negatives."""

import argparse
import json
from pathlib import Path
from statistics import median


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area = lambda x: max(0, x[2] - x[0]) * max(0, x[3] - x[1])
    return intersection / max(area(a) + area(b) - intersection, 1e-12)


def evaluate(dataset, results):
    rows = {r["file"]: r for r in results["rows"]}
    scored = []
    for photo in dataset["photos"]:
        if photo["file"] not in rows:
            continue
        row = rows[photo["file"]]
        predictions = row["matches"]
        regions = photo["regions"]
        links = sorted(
            (
                (iou(p["box"], r["box"]), pi, ri)
                for pi, p in enumerate(predictions)
                for ri, r in enumerate(regions)
            ),
            reverse=True,
        )
        used_p, used_r, pairs = set(), set(), {}
        for overlap, pi, ri in links:
            if overlap >= 0.5 and pi not in used_p and ri not in used_r:
                pairs[ri] = pi
                used_p.add(pi)
                used_r.add(ri)
        positive = [i for i, r in enumerate(regions) if r.get("productId")]
        correct, wrong, misses, ambiguous = [], [], [], []
        verified_predictions = set()
        for ri in positive:
            pi = pairs.get(ri)
            if pi is None:
                misses.append(regions[ri]["number"])
            elif predictions[pi].get("alternativeWineIds") and regions[ri][
                "productId"
            ] in [predictions[pi]["wineId"], *predictions[pi]["alternativeWineIds"]]:
                ambiguous.append(regions[ri]["number"])
                verified_predictions.add(pi)
            elif predictions[pi]["wineId"] == regions[ri]["productId"]:
                correct.append(regions[ri]["number"])
                verified_predictions.add(pi)
            else:
                wrong.append(regions[ri]["number"])
                verified_predictions.add(pi)
        rejected = []
        for ri, pi in pairs.items():
            possible = {
                predictions[pi]["wineId"],
                *predictions[pi].get("alternativeWineIds", []),
            }
            if regions[ri].get("notBottle") or possible.issubset(
                regions[ri].get("rejectedIds", [])
            ):
                if ri not in positive:
                    rejected.append(regions[ri]["number"])
                    verified_predictions.add(pi)
        scored.append(
            {
                "file": photo["file"],
                "split": photo["split"],
                "positives": len(positive),
                "correct": len(correct),
                "ambiguousKnown": len(ambiguous),
                "wrong": len(wrong) + len(rejected),
                "unreviewedAccepted": len(predictions) - len(verified_predictions),
                "accepted": len(predictions),
                "missedRegions": misses,
                "wrongRegions": wrong + rejected,
                "seconds": row.get(
                    "endToEndSeconds",
                    row.get("httpSeconds", row["timingsMs"]["processing"] / 1000),
                ),
            }
        )
    summary = {}
    for split in ["all", "development", "regression"]:
        subset = [r for r in scored if split == "all" or r["split"] == split]
        summary[split] = {
            k: sum(r[k] for r in subset)
            for k in [
                "positives",
                "correct",
                "ambiguousKnown",
                "wrong",
                "unreviewedAccepted",
                "accepted",
            ]
        }
        if subset:
            summary[split].update(
                photos=len(subset),
                medianSeconds=median(r["seconds"] for r in subset),
                maxSeconds=max(r["seconds"] for r in subset),
            )
    return {
        "independentGroundTruth": False,
        "completeNegatives": False,
        "summary": summary,
        "rows": scored,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    p.add_argument("--dataset", type=Path, default=Path("evaluation/shelves-v1.json"))
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = evaluate(
        json.loads(a.dataset.read_text()), json.loads(a.results.read_text())
    )
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
