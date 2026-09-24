"""Offline parity/evaluation of browser learned pipeline; no manual review used for inference."""

import argparse
import json
from pathlib import Path
import sys
import types

import cv2
import numpy as np
from PIL import Image
import torch
from prepare_learned import canonical


def main():
    cv2.setNumThreads(2)
    torch.set_num_threads(2)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "models",
        "photos",
        "detections",
        "onnx-source",
        "matcher-weights",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.onnx_source))
    from learned_model import Portable
    from export_learned import cross_attention
    from lightglue_dynamo.models.lightglue import LightGlue

    extractor = Portable().eval().cuda()
    matcher = LightGlue(args.matcher_weights, input_dim=128, n_layers=5).eval().cuda()
    for layer in matcher.transformers:
        layer.cross_attn.forward = types.MethodType(cross_attention, layer.cross_attn)
    index = json.loads((args.models / "local-index.json").read_text())
    centers = torch.tensor(
        np.fromfile(args.models / "centers.bin", "<f4").reshape(32, 128), device="cuda"
    )
    vectors = torch.tensor(
        np.fromfile(args.models / "vectors.bin", "i1")
        .reshape(-1, 2, 4096)
        .astype(np.float32),
        device="cuda",
    )
    vectors = torch.nn.functional.normalize(vectors, dim=-1)
    refs = {}

    def reference(slug):
        if slug not in refs:
            raw = (args.models / index["references"][slug]).read_bytes()
            count, width, height, dim = np.frombuffer(raw, "<u4", count=4)
            k = (
                np.frombuffer(raw, "<f4", count=int(count) * 2, offset=16)
                .reshape(-1, 2)
                .copy()
            )
            d = (
                np.frombuffer(raw, "i1", offset=16 + int(count) * 8)
                .astype(np.float32)
                .reshape(-1, 128)
                / 127
            )
            d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
            refs[slug] = {"k": k, "d": d, "size": np.array([width, height])}
        return refs[slug]

    def vlad(f, label=False):
        desc = f["d"] if not label else f["d"][f["k"][:, 1] > f["size"][1] * 0.35]
        desc = torch.tensor(desc, device="cuda")
        assignment = torch.cdist(desc, centers).argmin(1)
        value = torch.zeros_like(centers).index_add_(
            0, assignment, desc - centers[assignment]
        )
        value = torch.nn.functional.normalize(value, dim=1).flatten()
        value = value.sign() * value.abs().sqrt()
        return torch.nn.functional.normalize(value, dim=0)

    def geometry(q, r, pairs):
        empty = {"inliers": 0, "matches": len(pairs), "coverage": 0.0}
        if len(pairs) < 8:
            return empty
        h, mask = cv2.findHomography(
            r["k"][pairs[:, 1]],
            q["k"][pairs[:, 0]],
            cv2.RANSAC,
            3,
            maxIters=2000,
            confidence=0.995,
        )
        if h is None or mask.sum() < 8:
            return empty
        good = pairs[mask[:, 0] > 0]
        coverage = min(
            float(np.prod(np.ptp(f["k"][good[:, i]], axis=0)) / np.prod(f["size"]))
            for i, f in enumerate((q, r))
        )
        return {"inliers": int(mask.sum()), "matches": len(pairs), "coverage": coverage}

    def coarse(q, r):
        if min(len(q["k"]), len(r["k"])) < 8:
            return {"inliers": 0, "matches": 0, "coverage": 0.0}
        pairs = np.array(
            [
                [m.queryIdx, m.trainIdx]
                for m in cv2.BFMatcher(cv2.NORM_L2, crossCheck=True).match(
                    q["d"], r["d"]
                )
                if m.distance < np.sqrt(1.3)
            ],
            dtype=int,
        )
        return geometry(q, r, pairs)

    def learned(q, r):
        keys = np.zeros((2, 256, 2), np.float32)
        descs = np.zeros((2, 256, 128), np.float32)
        for i, f in enumerate((q, r)):
            n = min(256, len(f["k"]))
            keys[i, :n] = (f["k"][:n] - f["size"] / 2) / (max(f["size"]) / 2)
            descs[i, :n] = f["d"][:n]
        with torch.inference_mode():
            matches, _ = matcher(
                torch.tensor(keys, device="cuda"), torch.tensor(descs, device="cuda")
            )
        pairs = matches.cpu().numpy()[:, 1:]
        pairs = pairs[(pairs[:, 0] < len(q["k"])) & (pairs[:, 1] < len(r["k"]))]
        return geometry(q, r, pairs)

    source = json.loads(args.detections.read_text())
    rows = source["rows"] if isinstance(source, dict) else source
    output = []
    for row in rows:
        image = Image.open(args.photos / row["file"]).convert("RGB")
        found = set()
        proposals = []
        for p in row["proposals"]:
            crop = canonical(image.crop(tuple(map(round, p["originalBox"]))))
            tensor = (
                torch.from_numpy(np.array(crop)).permute(2, 0, 1)[None].float().cuda()
                / 255
            )
            with torch.inference_mode():
                k, d, s = extractor(tensor)
            valid = s[0].cpu().numpy() > 0.2
            q = {
                "k": k[0].cpu().numpy()[valid],
                "d": d[0].cpu().numpy()[valid],
                "size": np.array(crop.size),
            }
            scores = (
                torch.maximum(vectors[:, 0] @ vlad(q), vectors[:, 1] @ vlad(q, True))
                .cpu()
                .numpy()
            )
            order = np.argsort(-scores)
            ids = [index["ids"][i] for i in order[:24]]
            candidates = [
                {"id": slug, **coarse(q, reference(slug))}
                for slug in dict.fromkeys(ids + sorted(found))
            ]
            candidates.sort(key=lambda c: c["inliers"], reverse=True)
            finalists = list(dict.fromkeys([c["id"] for c in candidates[:3]] + ids[:2]))
            evidence = [
                {"id": slug, **learned(q, reference(slug))} for slug in finalists
            ]
            evidence.sort(key=lambda e: e["inliers"], reverse=True)
            best = evidence[0]
            gap = best["inliers"] - evidence[1]["inliers"]
            accepted = (
                best["inliers"] >= 20
                and best["inliers"] / max(best["matches"], 1) >= 0.35
                and best["coverage"] >= 0.025
                and gap >= 8
            )
            if accepted:
                found.add(best["id"])
            proposals.append(
                {
                    "number": p["number"],
                    "box": p["box"],
                    "id": best["id"] if accepted else None,
                    "evidence": evidence,
                    "retrieval": ids,
                    "coarse": candidates[:5],
                }
            )
            print(row["file"], p["number"], best["inliers"], gap, accepted, flush=True)
        output.append({"file": row["file"], "proposals": proposals})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "runtime": "PyTorch portable browser pipeline",
                    "manualOverrides": False,
                    "rows": output,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
