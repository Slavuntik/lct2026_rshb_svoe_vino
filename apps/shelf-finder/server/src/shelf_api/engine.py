"""Native GPU implementation of the browser hybrid pipeline, with a fully resident gallery."""

import hashlib
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
import torch

from .detection import detect
from .policy import independent, plausible, select


@dataclass
class Features:
    points: np.ndarray
    descriptors: torch.Tensor
    size: tuple[int, int]


class ShelfEngine:
    def __init__(self, directory: Path, device="cuda", threads=4):
        self.directory = directory.resolve()
        self.device = "cuda:0"
        if device not in ("cuda", "cuda:0"):
            raise ValueError(
                "Use CUDA_VISIBLE_DEVICES to select one GPU; CPU serving is not supported"
            )
        device = self.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA is required for this service")
        torch.set_num_threads(threads)
        cv2.setNumThreads(threads)
        self.manifest = json.loads((directory / "server-manifest.json").read_text())
        if self.manifest.get("version") != 1:
            raise ValueError("Unsupported server bundle")
        for name, expected in self.manifest["hashes"].items():
            if hashlib.sha256(self.asset(name).read_bytes()).hexdigest() != expected:
                raise ValueError("Corrupt asset: " + name)
        for name in [
            "catalog.json",
            "local-index.json",
            "centers.bin",
            "vectors.bin",
            "retriever.pt",
            "extractor.pt",
            "matcher.pt",
            self.manifest["detector"],
        ]:
            if name not in self.manifest["hashes"]:
                raise ValueError("Missing asset checksum: " + name)
        self.catalog = json.loads(self.asset("catalog.json").read_text())
        self.catalog_version = hashlib.sha256(
            self.asset("catalog.json").read_bytes()
        ).hexdigest()[:16]
        self.pipeline_version = (
            self.manifest["pipelineVersion"]
            + "-"
            + hashlib.sha256(
                (directory / "server-manifest.json").read_bytes()
                + b"".join(
                    Path(__file__).with_name(name).read_bytes()
                    for name in ("engine.py", "policy.py", "detection.py")
                )
            ).hexdigest()[:12]
        )
        self.wines = {w["id"]: w for w in self.catalog["wines"]}
        self.index = json.loads(self.asset("local-index.json").read_text())
        self.ids = self.index["ids"]
        if (
            self.ids != list(self.wines)
            or len(self.ids) != self.manifest["catalogSize"]
        ):
            raise ValueError("Mismatched catalog order")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        self.detector = ort.InferenceSession(
            str(self.asset(self.manifest["detector"])),
            options,
            providers=["CPUExecutionProvider"],
        )
        self.extractor = torch.jit.load(
            str(self.asset("extractor.pt")), map_location=device
        ).eval()
        self.retriever = torch.jit.load(
            str(self.asset("retriever.pt")), map_location=device
        ).eval()
        self.matcher = torch.jit.load(
            str(self.asset("matcher.pt")), map_location=device
        ).eval()
        self.centers = torch.tensor(
            np.fromfile(self.asset("centers.bin"), "<f4").reshape(32, 128),
            device=device,
        )
        self.vectors = torch.tensor(
            np.fromfile(self.asset("vectors.bin"), "i1")
            .astype("float32")
            .reshape(len(self.ids), 2, 4096),
            device=device,
        )
        self.vectors = torch.nn.functional.normalize(self.vectors, dim=-1)
        self.refs = {}
        self.verification = {}
        for slug in self.ids:
            self.refs[slug] = self.load_reference(self.index["references"][slug], 64)
            self.verification[slug] = self.load_reference(
                self.index["verificationReferences"][slug], 128
            )
        self.id_index = {slug: i for i, slug in enumerate(self.ids)}
        self.reference_descriptors = torch.zeros(len(self.ids), 512, 64, device=device)
        for i, slug in enumerate(self.ids):
            self.reference_descriptors[i, : len(self.refs[slug].points)] = self.refs[
                slug
            ].descriptors
        with torch.inference_mode():
            for width in range(32, 513, 32):
                image = torch.zeros(1, 3, 512, width, device=device)
                self.extractor(image)
                self.retriever(image)
            for pairs in [1, 2, 3, 4, 5, 8]:
                self.matcher(
                    torch.zeros(2 * pairs, 256, 2, device=device),
                    torch.zeros(2 * pairs, 256, 64, device=device),
                )
        self.detector.run(
            None,
            {
                self.detector.get_inputs()[0].name: np.zeros(
                    (1, 3, 640, 640), np.float32
                )
            },
        )
        self.sync()

    def asset(self, name):
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("Asset outside bundle")
        return path

    def sync(self):
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()

    def load_reference(self, name, dimension):
        if name not in self.manifest["hashes"]:
            raise ValueError("Reference missing checksum")
        raw = self.asset(name).read_bytes()
        if len(raw) < 16:
            raise ValueError("Invalid reference")
        count, width, height, dim = map(int, np.frombuffer(raw, "<u4", count=4))
        if (
            dim != dimension
            or count > 512
            or width < 1
            or height != 512
            or len(raw) != 16 + count * (8 + dim)
        ):
            raise ValueError("Invalid reference dimensions")
        points = (
            np.frombuffer(raw, "<f4", count=count * 2, offset=16).reshape(-1, 2).copy()
        )
        descriptors = torch.tensor(
            np.frombuffer(raw, "i1", offset=16 + count * 8)
            .astype("float32")
            .reshape(count, dim)
            / 127,
            device=self.device,
        )
        return Features(
            points, torch.nn.functional.normalize(descriptors, dim=-1), (width, height)
        )

    def extract(self, image, box, model, threshold):
        x0, y0, x1, y1 = [
            v * (image.width if i % 2 == 0 else image.height) for i, v in enumerate(box)
        ]
        width = max(32, min(512, round((x1 - x0) * 512 / (y1 - y0))))
        padded = Image.new("RGB", (math.ceil(width / 32) * 32, 512), "white")
        # Floating source box avoids losing a pixel to an intermediate integer crop.
        resized = image.resize(
            (width, 512), Image.Resampling.BILINEAR, box=(x0, y0, x1, y1)
        )
        padded.paste(resized, ((padded.width - width) // 2, 0))
        tensor = (
            torch.tensor(np.array(padded), device=self.device)
            .permute(2, 0, 1)[None]
            .float()
            / 255
        )
        k, d, s = model(tensor)
        valid = s[0] > threshold
        return Features(k[0][valid].cpu().numpy(), d[0][valid], padded.size)

    def vlad(self, f, label):
        descriptors = f.descriptors
        if label:
            descriptors = descriptors[
                torch.tensor(f.points[:, 1] > f.size[1] * 0.35, device=self.device)
            ]
        assignment = torch.cdist(descriptors, self.centers).argmin(1)
        value = torch.zeros_like(self.centers).index_add_(
            0, assignment, descriptors - self.centers[assignment]
        )
        value = torch.nn.functional.normalize(value, dim=1).flatten()
        value = value.sign() * value.abs().sqrt()
        return torch.nn.functional.normalize(value, dim=0)

    def retrieve(self, f):
        views = torch.stack([self.vlad(f, False), self.vlad(f, True)])
        scores = (self.vectors * views).sum(-1).amax(-1).cpu().numpy()
        return [self.ids[i] for i in np.argsort(-scores, kind="stable")]

    def geometry(self, q, r, pairs, coarse=False):
        empty = {"inliers": 0, "matches": len(pairs), "coverage": 0.0}
        if len(pairs) < 8:
            return empty
        pairs = np.asarray(pairs, dtype=np.int32)
        h, mask = cv2.findHomography(
            r.points[pairs[:, 1]],
            q.points[pairs[:, 0]],
            cv2.RHO if coarse else cv2.RANSAC,
            3,
            maxIters=512 if coarse else 2000,
            confidence=0.995,
        )
        if h is None or mask is None or not plausible(h, r.size, q.size):
            return empty
        good = pairs[mask.ravel() > 0]
        if len(good) < 8:
            return empty
        coverage = min(
            float(np.prod(np.ptp(f.points[good[:, i]], axis=0)) / np.prod(f.size))
            for i, f in enumerate([q, r])
        )
        return {"inliers": len(good), "matches": len(pairs), "coverage": coverage}

    def coarse(self, q, r, robust=False):
        if min(len(q.points), len(r.points)) < 8:
            return {"inliers": 0, "matches": 0, "coverage": 0.0}
        similarities = q.descriptors @ r.descriptors.T
        values, neighbors = similarities.max(1)
        reverse = similarities.argmax(0)
        rows = torch.arange(len(q.points), device=self.device)
        valid = (reverse[neighbors] == rows) & (values > 0.35)
        rows = rows[valid]
        cols = neighbors[valid]
        order = values[valid].argsort(descending=True, stable=True)
        pairs = torch.stack([rows[order], cols[order]], dim=1).cpu().numpy()
        return self.geometry(q, r, pairs, not robust)

    def coarse_many(self, q, ids):
        refs = self.reference_descriptors[
            torch.tensor([self.id_index[slug] for slug in ids], device=self.device)
        ]
        similarities = q.descriptors @ refs.transpose(1, 2)
        values, neighbors = similarities.max(2)
        reverse = similarities.argmax(1)
        rows = torch.arange(len(q.points), device=self.device)[None].expand(
            len(ids), -1
        )
        valid = (reverse.gather(1, neighbors) == rows) & (values > 0.35)
        batch, row = torch.where(valid)
        packed = (
            torch.stack(
                [
                    batch.float(),
                    row.float(),
                    neighbors[batch, row].float(),
                    values[batch, row],
                ],
                dim=1,
            )
            .cpu()
            .numpy()
        )
        evidence = []
        for b, slug in enumerate(ids):
            selected = packed[packed[:, 0] == b]
            selected = selected[np.argsort(-selected[:, 3], kind="stable")]
            evidence.append(
                {
                    "id": slug,
                    **self.geometry(
                        q, self.refs[slug], selected[:, 1:3].astype(np.int32), True
                    ),
                }
            )
        return evidence

    def learned_many(self, q, ids):
        if not ids:
            return []
        k = torch.zeros(2 * len(ids), 256, 2, device=self.device)
        d = torch.zeros(2 * len(ids), 256, 64, device=self.device)
        for b, slug in enumerate(ids):
            for offset, f in enumerate([q, self.refs[slug]]):
                n = min(256, len(f.points))
                points = (f.points[:n] - np.array(f.size) / 2) / (max(f.size) / 2)
                k[2 * b + offset, :n] = torch.tensor(
                    points, device=self.device, dtype=torch.float32
                )
                d[2 * b + offset, :n] = f.descriptors[:n]
        matches, _ = self.matcher(k, d)
        matches = matches.cpu().numpy()
        evidence = []
        for b, slug in enumerate(ids):
            pairs = matches[matches[:, 0] == b][:, 1:]
            pairs = pairs[
                (pairs[:, 0] < len(q.points))
                & (pairs[:, 1] < len(self.refs[slug].points))
            ]
            evidence.append({"id": slug, **self.geometry(q, self.refs[slug], pairs)})
        return evidence

    def resolve(self, query, evidence, repeated=False):
        ordered = sorted(evidence, key=lambda e: -e["inliers"])
        accepted = select(ordered, repeated)
        if accepted:
            return (
                accepted
                if independent(self.coarse(query, self.verification[accepted], True))
                else None
            )
        # A close primary tie can be resolved only by a clear independent winner
        # that agrees with the primary winner. No SKU-specific rules or lower thresholds.
        if (
            len(ordered) > 1
            and select(ordered[:1], repeated)
            and ordered[0]["inliers"] - ordered[1]["inliers"] < 8
        ):
            corroboration = [
                {"id": e["id"], **self.coarse(query, self.verification[e["id"]], True)}
                for e in ordered[:3]
            ]
            corroborated = select(corroboration)
            if corroborated == ordered[0]["id"]:
                return corroborated
        return None

    @torch.inference_mode()
    def scan(self, image, diagnostics=False):
        self.sync()
        started = time.perf_counter()
        cv2.setRNGSeed(2026)
        image = image.copy()
        image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        detections = detect(image, self.detector)
        detection_ms = (time.perf_counter() - started) * 1000
        found = []
        observations = []
        for proposal in detections:
            box = proposal["box"]
            too_small = (box[2] - box[0]) * image.width < 40 or (
                box[3] - box[1]
            ) * image.height < 80
            item = {**proposal, "tooSmall": too_small, "id": None, "evidence": []}
            observations.append(item)
            if too_small:
                continue
            q = self.extract(image, box, self.extractor, 0)
            if len(q.points) < 16:
                continue
            retrieval_q = self.extract(image, box, self.retriever, 0.2)
            ranking = self.retrieve(retrieval_q)
            item.update(q=q, retrieval_q=retrieval_q, shortlist=set(ranking[:100]))
            ids = list(dict.fromkeys(ranking[:24] + found))
            coarse = self.coarse_many(q, ids)
            coarse.sort(key=lambda e: -e["inliers"])
            if not coarse or coarse[0]["inliers"] < 12:
                continue
            finalists = list(dict.fromkeys([e["id"] for e in coarse[:3]] + ranking[:2]))
            evidence = self.learned_many(q, finalists)
            evidence.sort(key=lambda e: -e["inliers"])
            item["evidence"] = evidence
            accepted = self.resolve(retrieval_q, evidence)
            if accepted:
                item["id"] = accepted
                if accepted not in found and len(found) < 32:
                    found.append(accepted)
        for item in observations:
            if item["id"] or "q" not in item:
                continue
            q = item["q"]
            evidence = item["evidence"][:]
            pending = []
            for slug in found:
                if any(e["id"] == slug for e in evidence):
                    continue
                if (
                    slug not in item["shortlist"]
                    and self.coarse(q, self.refs[slug])["inliers"] < 8
                ):
                    continue
                pending.append(slug)
            evidence.extend(self.learned_many(q, pending))
            accepted = self.resolve(item["retrieval_q"], evidence, True)
            if accepted in found:
                item["id"] = accepted
                item["evidence"] = evidence
        self.sync()
        elapsed = (time.perf_counter() - started) * 1000
        matches = [
            {"box": o["box"], "wineId": o["id"], "name": self.wines[o["id"]]["name"]}
            for o in observations
            if o["id"]
        ]
        result = {
            "pipelineVersion": self.pipeline_version,
            "catalogVersion": self.catalog_version,
            "detectedCount": len(detections),
            "matches": matches,
            "timingsMs": {
                "queue": 0,
                "processing": elapsed,
                "detection": detection_ms,
                "recognition": elapsed - detection_ms,
            },
            "warnings": [],
        }
        if diagnostics:
            result["observations"] = [
                {
                    k: v
                    for k, v in o.items()
                    if k not in ("q", "retrieval_q", "shortlist")
                }
                for o in observations
            ]
        return result
