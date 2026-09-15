"""Оценка слоя 1 на синтетике: насколько рамка детектора совпадает с настоящей (IoU).

Запуск: ``python -m winescan.eval.detector_eval --split synth_v1 --limit 400``

Детектор запускается один раз, все рамки сохраняются в detections.jsonl; стратегии выбора
главной упаковки сравниваются уже по сохранённым рамкам.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from winescan.config import get_paths
from winescan.eval.run import _load_query, load_split
from winescan.logging_setup import setup_logging
from winescan.vision.detector import Detection, PackageDetector, choose_main_package

STRATEGIES = {
    "v1_area_centrality": {"min_score": 0.0, "area_cap": 1.0, "centrality_floor": 0.5, "group_penalty": 1.0},
    "v2_capped_group_penalty": {},
    "top_score": None,
}


def iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="IoU детектора упаковки на синтетике")
    parser.add_argument("--split", default="synth_v1")
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--device", default=None)
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    manifest, images_dir = load_split(args.split)
    manifest = manifest.sample(n=min(args.limit, len(manifest)), random_state=0)
    detector = PackageDetector(device=args.device)
    out_dir = paths.artifacts_dir / "eval" / f"detector__{args.split}__limit{args.limit}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, latencies = [], []
    with (out_dir / "detections.jsonl").open("w", encoding="utf-8") as fh:
        for row in manifest.itertuples(index=False):
            image, scale = _load_query(images_dir / row.image_path)
            began = time.perf_counter()
            detections = detector.detect(image)
            latencies.append((time.perf_counter() - began) * 1000)
            truth = tuple(float(v) * scale for v in row.bbox.split(","))
            fh.write(json.dumps({"query_id": row.query_id, "truth": truth, "size": image.size,
                                 "detections": [[*d.box, d.score, d.label] for d in detections]}) + "\n")  # fmt: skip
            result = {"query_id": row.query_id, "n_detections": len(detections)}
            for name, params in STRATEGIES.items():
                if params is None:
                    chosen = max(detections, key=lambda d: d.score) if detections else None
                else:
                    chosen = choose_main_package(detections, image.size, **params)
                result[name] = iou(chosen.box, truth) if chosen else 0.0
            rows.append(result)

    frame = pd.DataFrame(rows)
    summary = {
        name: {"mean_iou": float(frame[name].mean()), "iou_ge_0_5": float((frame[name] >= 0.5).mean()),
               "iou_ge_0_7": float((frame[name] >= 0.7).mean())}
        for name in STRATEGIES
    }  # fmt: skip
    summary["latency_ms"] = {"mean": float(np.mean(latencies)), "p95": float(np.percentile(latencies, 95))}
    frame.to_csv(out_dir / "iou.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("| стратегия | средний IoU | IoU ≥ 0,5 | IoU ≥ 0,7 |\n|---|---|---|---|")
    for name in STRATEGIES:
        s = summary[name]
        print(f"| {name} | {s['mean_iou']:.3f} | {s['iou_ge_0_5']:.3f} | {s['iou_ge_0_7']:.3f} |")
    print(f"задержка детектора: среднее {summary['latency_ms']['mean']:.0f} мс, p95 {summary['latency_ms']['p95']:.0f} мс")


if __name__ == "__main__":
    main()
