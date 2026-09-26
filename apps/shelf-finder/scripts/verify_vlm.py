"""Separate Qwen suggestions from matches supported by existing local geometry.

This offline experiment reads model diagnostics, never ground-truth labels.
Thresholds are imported unchanged from the native recognizer. Missing evidence
means unverified, not a negative catalog claim. No model/network calls are made.
"""

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server/src"))
from shelf_api.policy import select_strong
from evaluate_shelves import iou
from evaluate_vlm import proposals


def verified_by_geometry(wine_id, observation):
    if observation.get("id"):
        return "native_accepted" if observation["id"] == wine_id else None
    evidence = observation.get("rescueEvidence", [])
    if select_strong(evidence) == wine_id:
        return "strong_geometry"
    return None


def verify(raw, native):
    result = copy.deepcopy(raw)
    result["mode"] = raw["mode"] + "-geometry-checked"
    native_rows = {r["file"]: r for r in native["rows"]}
    counts = Counter()
    for row in result["rows"]:
        if row.get("complete") is False:
            raise ValueError("Unfinished VLM run")
        valid, _ = proposals(row)
        row["suggestions"] = copy.deepcopy(row["parsed"]["bottles"])
        crops = {c["crop_id"]: c for c in row["crops"]}
        for b in row["parsed"]["bottles"]:
            if not isinstance(b, dict) or type(b.get("crop_id")) is not int:
                continue
            slug = b.get("selected_id")
            crop = crops.get(b.get("crop_id"))
            if not slug or not crop:
                continue
            # Respect response validation before looking for geometric confirmation.
            allowed = any(
                m["wineId"] == slug and m["box"] == crop["box"]
                for m in valid["matches"]
            )
            observations = native_rows[row["file"]]["observations"]
            observation = max(
                observations, key=lambda o: iou(o["box"], crop["box"]), default=None
            )
            reason = None
            if allowed and observation and iou(observation["box"], crop["box"]) >= 0.99:
                reason = verified_by_geometry(slug, observation)
                ambiguous = any(
                    m.get("alternativeWineIds") and iou(m["box"], crop["box"]) >= 0.99
                    for m in native_rows[row["file"]]["matches"]
                )
                if ambiguous:
                    reason = None
            b["confirmation"] = reason or "unverified"
            if reason:
                counts[reason] += 1
            else:
                counts["unverified"] += 1
                b["selected_id"] = None
    result["verificationCounts"] = dict(counts)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    p.add_argument("--native-results", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = verify(
        json.loads(a.results.read_text()), json.loads(a.native_results.read_text())
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["verificationCounts"])
