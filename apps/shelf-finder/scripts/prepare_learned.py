"""Build a browser-only ALIKED/VLAD gallery from organizer references, never shelf labels."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from PIL import Image
import torch


def canonical(image):
    image = image.convert("RGB")
    width = max(32, min(512, round(image.width * 512 / image.height)))
    resized = image.resize((width, 512), Image.Resampling.BILINEAR)
    padded = Image.new("RGB", (math.ceil(width / 32) * 32, 512), "white")
    padded.paste(resized, ((padded.width - width) // 2, 0))
    return padded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in (
        "catalog",
        "reference-cache",
        "onnx-source",
        "baseline",
        "output",
        "cache",
    ):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--extractor", choices=["aliked", "xfeat"], default="aliked")
    parser.add_argument("--xfeat-source", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.onnx_source.resolve()))
    if args.extractor == "xfeat":
        if not args.xfeat_source:
            parser.error("--xfeat-source is required for XFeat")
        sys.path.insert(0, str(args.xfeat_source.resolve()))
        from xfeat_model import PortableXFeat

        model = (
            PortableXFeat(args.xfeat_source / "weights/xfeat.pt").eval().to(args.device)
        )
        dimension, score_threshold = 64, 0
    else:
        from learned_model import Portable

        model = Portable().eval().to(args.device)
        dimension, score_threshold = 128, 0.2
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in args.catalog.read_text().splitlines()]
    # A different model or preprocessing must never reuse a stale feature cache.
    cache_hash = hashlib.sha256(
        json.dumps(
            {
                "extractor": args.extractor,
                "dimension": dimension,
                "height": 512,
                "scoreThreshold": score_threshold,
                "padding": "center-white-v1",
            }
        ).encode()
    )
    for key, tensor in sorted(model.state_dict().items()):
        cache_hash.update(key.encode())
        cache_hash.update(tensor.detach().cpu().numpy().tobytes())
    model_source = Path(__file__).with_name(
        "xfeat_model.py" if args.extractor == "xfeat" else "learned_model.py"
    )
    cache_hash.update(model_source.read_bytes())
    cache_prefix = cache_hash.hexdigest()[:16]
    all_features = []
    for index, row in enumerate(rows):
        slug = row["slug"]
        # Hash filenames rather than interpolating catalog strings into output paths.
        filename = hashlib.sha256(slug.encode()).hexdigest()[:24]
        cache = args.cache / (cache_prefix + "-" + filename + ".npz")
        if not cache.exists():
            source = args.reference_cache / (slug + ".png")
            if source.resolve().parent != args.reference_cache.resolve():
                raise ValueError("Invalid catalog slug")
            with Image.open(source) as image:
                image = canonical(image)
                tensor = (
                    torch.from_numpy(np.array(image))
                    .permute(2, 0, 1)[None]
                    .float()
                    .to(args.device)
                    / 255
                )
            with torch.inference_mode():
                k, d, scores = model(tensor)
            valid = scores[0].cpu().numpy() > score_threshold
            np.savez(
                cache,
                k=k[0].cpu().numpy()[valid],
                d=d[0].cpu().numpy()[valid],
                size=np.array(image.size),
            )
        all_features.append(dict(np.load(cache)))
        if index % 100 == 0:
            print(f"References: {index}/{len(rows)}", flush=True)
    rng = np.random.default_rng(2026)
    samples = np.concatenate(
        [
            f["d"][rng.choice(len(f["d"]), min(48, len(f["d"])), replace=False)]
            for f in all_features
            if len(f["d"])
        ]
    )
    samples = torch.tensor(samples, device=args.device)
    centers = samples[
        torch.tensor(rng.choice(len(samples), 32, replace=False), device=args.device)
    ].clone()
    for _ in range(30):
        assignments = torch.cdist(samples, centers).argmin(1)
        counts = torch.bincount(assignments, minlength=32)
        sums = torch.zeros_like(centers).index_add_(0, assignments, samples)
        centers = torch.where(
            counts[:, None] > 0, sums / counts.clamp(min=1)[:, None], centers
        )

    def vlad(features, label):
        d = features["d"]
        if label:
            d = d[features["k"][:, 1] > features["size"][1] * 0.35]
        d = torch.tensor(d, device=args.device)
        assignment = torch.cdist(d, centers).argmin(1)
        v = torch.zeros_like(centers).index_add_(0, assignment, d - centers[assignment])
        v = torch.nn.functional.normalize(v, dim=1).flatten()
        v = v.sign() * v.abs().sqrt()
        return torch.nn.functional.normalize(v, dim=0).cpu().numpy()

    vectors = np.stack([[vlad(f, False), vlad(f, True)] for f in all_features])
    centers.cpu().numpy().astype("<f4").tofile(args.output / "centers.bin")
    # Symmetric int8 quantization; the browser renormalizes each vector before comparison.
    np.rint(
        vectors
        / np.maximum(np.max(np.abs(vectors), axis=-1, keepdims=True), 1e-12)
        * 127
    ).clip(-127, 127).astype("i1").tofile(args.output / "vectors.bin")
    references = {}
    (args.output / "references").mkdir(exist_ok=True)
    for row, features in zip(rows, all_features):
        filename = (
            "references/"
            + hashlib.sha256(row["slug"].encode()).hexdigest()[:24]
            + ".bin"
        )
        n = len(features["k"])
        with (args.output / filename).open("wb") as out:
            np.array([n, *features["size"], dimension], dtype="<u4").tofile(out)
            features["k"].astype("<f4").tofile(out)
            np.rint(features["d"] * 127).clip(-127, 127).astype("i1").tofile(out)
        references[row["slug"]] = filename
    model_id = (
        args.extractor
        + "-vlad32-512-v1-"
        + hashlib.sha256((args.output / "centers.bin").read_bytes()).hexdigest()[:12]
    )
    catalog = {
        "version": 1,
        "embeddingModel": model_id,
        "dimension": 1,
        "wines": [
            {
                "id": r["slug"],
                "name": r["name"],
                "brand": r.get("winery") or "",
                "region": r.get("region") or "",
                "group": r.get("color") or "",
                "references": [[1]],
            }
            for r in rows
        ],
    }
    (args.output / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False))
    (args.output / "local-index.json").write_text(
        json.dumps(
            {
                "version": 1,
                "ids": [r["slug"] for r in rows],
                "references": references,
                "centers": "centers.bin",
                "vectors": "vectors.bin",
                "dimension": dimension,
                "clusters": 32,
                "points": 512,
                "extractor": args.extractor,
                "scoreThreshold": score_threshold,
            }
        )
    )
    baseline = json.loads((args.baseline / "manifest.json").read_text())
    shutil.copyfile(args.baseline / baseline["detector"], args.output / "detector.onnx")
    manifest = {
        "version": 1,
        "detector": "detector.onnx",
        "embedder": "xfeat.onnx" if args.extractor == "xfeat" else "aliked.onnx",
        "catalog": "catalog.json",
        "embeddingModel": model_id,
        "dimension": 1,
        "detectorSize": baseline["detectorSize"],
        "bottleClass": baseline["bottleClass"],
        "localFeatures": "local-index.json",
        "matcher": (
            "lighterglue.onnx" if args.extractor == "xfeat" else "lightglue.onnx"
        ),
        "localExtractor": args.extractor,
        "matcherPoints": 256,
        "matcherLayers": 6 if args.extractor == "xfeat" else 5,
        "hashes": {},
    }
    license_source = Path(__file__).resolve().parent.parent / "licenses"
    for path in license_source.glob("*.txt"):
        shutil.copyfile(path, args.output / path.name)
    for path in args.output.iterdir():
        if path.is_file() and path.name != "manifest.json":
            manifest["hashes"][path.name] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Prepared {len(rows)} references in {args.output}", flush=True)


if __name__ == "__main__":
    main()
