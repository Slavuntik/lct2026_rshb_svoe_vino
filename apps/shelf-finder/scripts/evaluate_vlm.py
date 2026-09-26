"""Posthoc partial-label scoring of VLM proposals; no labels enter model prompts."""

import argparse
from collections import Counter
import json
from pathlib import Path
from evaluate_shelves import evaluate, iou


def proposals(row):
    crops = {c["crop_id"]: c for c in row["crops"]}
    bottles = row.get("parsed", {}).get("bottles", [])
    counts = Counter(
        b.get("crop_id")
        for b in bottles
        if isinstance(b, dict) and isinstance(b.get("crop_id"), int)
    )
    matches = []
    invalid = []
    for b in bottles:
        if not isinstance(b, dict):
            invalid.append("invalid_entry")
            continue
        number = b.get("crop_id")
        if not isinstance(number, int) or number not in crops or counts[number] != 1:
            invalid.append("unknown_or_duplicate_crop")
            continue
        slug = b.get("selected_id")
        if slug is None:
            continue
        if not isinstance(slug, str) or slug not in crops[number]["candidates"]:
            invalid.append("out_of_shortlist")
            continue
        if not isinstance(b.get("evidence"), str) or not b["evidence"].strip():
            invalid.append("missing_evidence")
            continue
        matches.append({"wineId": slug, "box": crops[number]["box"]})
    return {
        "file": row["file"],
        "matches": matches,
        "timingsMs": {"processing": row["seconds"] * 1000},
    }, invalid


def report(dataset, raw, native):
    if any(row.get("complete") is False for row in raw["rows"]):
        raise ValueError("Cannot score an unfinished reference run")
    rows = []
    invalid = []
    selected = []
    native_by_file = {r["file"]: r for r in native["rows"]}
    for row in raw["rows"]:
        prediction, errors = proposals(row)
        rows.append(prediction)
        invalid.extend(errors)
        baseline = native_by_file[row["file"]]
        selected.append(
            {
                **baseline,
                "matches": [
                    m
                    for m in baseline["matches"]
                    if any(iou(m["box"], c["box"]) >= 0.99 for c in row["crops"])
                ],
            }
        )
    files = {r["file"] for r in raw["rows"]}
    subset = {"photos": []}
    for photo in dataset["photos"]:
        if photo["file"] not in files:
            continue
        row = next(r for r in raw["rows"] if r["file"] == photo["file"])
        subset["photos"].append(
            {
                **photo,
                "regions": [
                    r
                    for r in photo["regions"]
                    if any(iou(r["box"], c["box"]) >= 0.5 for c in row["crops"])
                ],
            }
        )
    return {
        "model": raw["model"],
        "mode": raw["mode"],
        "independentGroundTruth": False,
        "cropCount": sum(len(r["crops"]) for r in raw["rows"]),
        "invalidProposals": dict(Counter(invalid)),
        "requestErrors": sum(
            bool(request.get("error"))
            for row in raw["rows"]
            for request in row.get("requests", [row])
        ),
        "missingCropResponses": sum(
            len(
                {c["crop_id"] for c in row["crops"]}
                - {
                    b.get("crop_id")
                    for b in row.get("parsed", {}).get("bottles", [])
                    if isinstance(b, dict) and type(b.get("crop_id")) is int
                }
            )
            for row in raw["rows"]
        ),
        "selectedCropEvaluation": evaluate(subset, {"rows": rows}),
        "nativeOnSameCrops": evaluate(subset, {"rows": selected}),
        "wholePhotoEvaluation": evaluate(dataset, {"rows": rows}),
        "predictions": rows,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    p.add_argument("--dataset", type=Path, default=Path("evaluation/shelves-v1.json"))
    p.add_argument("--native-results", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = report(
        json.loads(a.dataset.read_text()),
        json.loads(a.results.read_text()),
        json.loads(a.native_results.read_text()),
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v["summary"]["all"]
                for k, v in result.items()
                if k.endswith("Evaluation") or k == "nativeOnSameCrops"
            },
            ensure_ascii=False,
            indent=2,
        )
    )
