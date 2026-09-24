"""Measure a GPU shelf candidate pipeline, not calibrated recognition accuracy or HTTP latency."""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import torch
from PIL import Image, ImageOps

from audit import detect
from winescan.search.multi import MultiIndexSearcher
from winescan.search.local_match import extract, match


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("photos", "catalog", "images", "detector", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=16)
    args = p.parse_args()
    if args.repeats < 1:
        p.error("--repeats must be positive")
    cv2.setNumThreads(4)
    torch.set_num_threads(4)
    started = time.perf_counter()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    detector = ort.InferenceSession(
        str(args.detector), options, providers=["CPUExecutionProvider"]
    )
    searcher = MultiIndexSearcher(
        [
            "siglip2-so400m-patch14-384__yaw-30_-15_0_15_30",
            "siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30",
        ],
        [0.5, 0.5],
        device="cuda",
    )
    cards = {
        r["slug"]: r for r in map(json.loads, args.catalog.read_text().splitlines())
    }
    if set(cards) != set(searcher.wine_slugs):
        raise ValueError("Catalog and index SKU sets differ")
    refs = {}
    init = time.perf_counter() - started
    print(
        json.dumps(
            {"initializationSeconds": init, "gpu": torch.cuda.get_device_name()}
        ),
        flush=True,
    )

    def reference(slug):
        if slug not in refs:
            path = (args.images / cards[slug]["image"]["file"]).resolve()
            if not path.is_relative_to(args.images.resolve()):
                raise ValueError("Reference outside image directory")
            with Image.open(path) as raw:
                refs[slug] = extract(ImageOps.exif_transpose(raw).convert("RGB"))
        return refs[slug]

    result = {
        "gpu": torch.cuda.get_device_name(),
        "detectorProvider": detector.get_providers(),
        "cpuThreads": 4,
        "batchSize": args.batch_size,
        "catalogSize": len(cards),
        "initializationSeconds": init,
        "engine": "YOLO11n dense CPU + SigLIP2 GPU full/label + SIFT/MAGSAC top10",
        "includes": [
            "photo decoding",
            "detection",
            "embedding",
            "catalog search",
            "geometry",
        ],
        "excludes": [
            "network",
            "request queue",
            "HTTP serialization",
            "calibrated accept/reject policy",
        ],
        "referenceCache": "lazy, filled by unmeasured first pass; query embeddings are never cached",
        "rows": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repeats + 1):
        for name in args.files:
            torch.cuda.synchronize()
            start = time.perf_counter()
            with Image.open(args.photos / name) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
            proposals = detect(image, detector, dense=True)
            detection_done = time.perf_counter()
            boxes = [r["box"] for r in proposals]
            vectors = searcher.embed_views(
                [image] * len(boxes), boxes, batch_size=args.batch_size
            )
            scores = searcher.wine_scores(vectors)
            torch.cuda.synchronize()
            retrieval_done = time.perf_counter()
            evidence = []
            for box, values in zip(boxes, scores):
                query = extract(image.crop(tuple(round(x) for x in box)))
                candidates = []
                for i in np.argsort(values)[::-1][:10]:
                    slug = searcher.wine_slugs[i]
                    verified = match(query, reference(slug))
                    candidates.append({"id": slug, "inliers": verified.inliers})
                evidence.append({"box": box, "candidates": candidates})
            torch.cuda.synchronize()
            end = time.perf_counter()
            row = {
                "file": name,
                "warmup": repeat == 0,
                "repeat": repeat,
                "bottles": len(boxes),
                "detectionSeconds": detection_done - start,
                "retrievalSeconds": retrieval_done - detection_done,
                "geometrySeconds": end - retrieval_done,
                "totalSeconds": end - start,
                "cachedReferences": len(refs),
                "evidence": evidence,
            }
            result["rows"].append(row)
            result["peakTorchVramMiB"] = torch.cuda.max_memory_allocated() / 2**20
            args.output.write_text(json.dumps(result, indent=2))
            print(
                json.dumps({k: v for k, v in row.items() if k != "evidence"}),
                flush=True,
            )
    times = [r["totalSeconds"] for r in result["rows"] if not r["warmup"]]
    result["summary"] = {
        "samples": len(times),
        "medianSeconds": float(np.median(times)),
        "minSeconds": min(times),
        "maxSeconds": max(times),
    }
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    main()
