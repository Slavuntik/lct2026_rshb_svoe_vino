"""Optional second pass: label views, stronger geometric matching, bounded candidate work."""

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from .engine import ShelfEngine, Features
from .policy import select_strong


class AccuracyEngine(ShelfEngine):
    def __init__(
        self,
        directory,
        accuracy_dir,
        device="cuda",
        threads=4,
        rescue_limit=8,
        budget_seconds=10,
        use_labels=True,
        use_ocr=False,
        semantic_dir=None,
        semantic_scope="selected",
        semantic_veto=True,
    ):
        if (
            semantic_scope not in ("all", "selected")
            or not 0 <= rescue_limit <= 80
            or not math.isfinite(budget_seconds)
            or budget_seconds <= 0
        ):
            raise ValueError("Invalid refinement limits")
        super().__init__(directory, device, threads)
        self.semantic_veto = semantic_veto
        self.semantic_scope = semantic_scope
        self.semantic = None
        self.semantic_rankings = {}
        if semantic_dir:
            from .semantic import SemanticRetriever

            self.semantic = SemanticRetriever(Path(semantic_dir), self.device)
            if set(self.semantic.ids) != set(self.ids):
                raise ValueError("Semantic catalog differs")
        self.rescue_limit = rescue_limit
        self.budget_seconds = budget_seconds
        self.use_labels = use_labels
        self.use_ocr = use_ocr
        accuracy_dir = Path(accuracy_dir).resolve()
        manifest = json.loads((accuracy_dir / "manifest.json").read_text())
        if (
            manifest["baseManifestSha256"]
            != hashlib.sha256(
                (directory / "server-manifest.json").read_bytes()
            ).hexdigest()
        ):
            raise ValueError("Accuracy bundle is incompatible with base models")
        for name, digest in manifest["hashes"].items():
            path = (accuracy_dir / name).resolve()
            if (
                not path.is_relative_to(accuracy_dir)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Invalid accuracy asset")
        self.reference_metadata = json.loads(
            (accuracy_dir / "references.json").read_text()
        )
        self.strong = torch.jit.load(
            str(accuracy_dir / "aliked-matcher.pt"), map_location=self.device
        ).eval()
        self.labels = {}
        for slug, file in self.reference_metadata["referenceFiles"].items():
            with np.load(accuracy_dir / file, allow_pickle=False) as f:
                self.labels[slug] = Features(
                    f["points"].copy(),
                    torch.tensor(f["descriptors"], device=self.device),
                    tuple(map(int, f["size"])),
                )
        self.label_vectors = torch.tensor(
            np.load(accuracy_dir / "label-vectors.npy", allow_pickle=False),
            device=self.device,
        )
        self.aliked_descriptors = torch.zeros(
            len(self.ids), 512, 128, device=self.device
        )
        for i, slug in enumerate(self.ids):
            self.aliked_descriptors[i, : len(self.verification[slug].points)] = (
                self.verification[slug].descriptors
            )
        self.ocr = None
        if use_ocr:
            from .text_check import TextCheck

            self.ocr = TextCheck(self.reference_metadata["catalogNames"])
        base_version = self.pipeline_version
        self.pipeline_version = (
            "accuracy-v2-"
            + hashlib.sha256(
                (accuracy_dir / "manifest.json").read_bytes()
                + base_version.encode()
                + b"".join(
                    Path(__file__).with_name(name).read_bytes()
                    for name in ["accuracy.py", "semantic.py", "text_check.py"]
                )
                + (
                    (Path(semantic_dir) / "manifest.json").read_bytes()
                    if semantic_dir
                    else b""
                )
                + f"{rescue_limit},{budget_seconds},{use_labels},{use_ocr},{bool(semantic_dir)},{semantic_scope},{semantic_veto}".encode()
            ).hexdigest()[:12]
        )
        with torch.inference_mode():
            for pairs in [1, 4]:
                self.strong(
                    torch.zeros(2 * pairs, 512, 2, device=self.device),
                    torch.zeros(2 * pairs, 512, 128, device=self.device),
                )
        if self.semantic:
            self.semantic.rank(
                Image.new("RGB", (128, 256), "white"), [[0, 0, 1, 1]] * 16
            )
        self.sync()

    def prepare_queries(self, image, detections):
        self.semantic_rankings = {}
        if self.semantic and self.semantic_scope == "all":
            boxes = [d["box"] for d in detections]
            self.semantic_rankings = dict(
                zip(map(tuple, boxes), self.semantic.rank(image, boxes))
            )

    def candidates(self, box, features):
        local = self.retrieve(features)
        semantic = self.semantic_rankings.get(tuple(box), [])
        return list(
            dict.fromkeys(semantic[:12] + local[:24] + semantic[12:] + local[24:])
        )

    def strong_many(self, query, ids, refs):
        if not ids or len(query.points) < 8:
            return []
        k = torch.zeros(2 * len(ids), 512, 2, device=self.device)
        d = torch.zeros(2 * len(ids), 512, 128, device=self.device)
        for b, slug in enumerate(ids):
            for offset, f in enumerate([query, refs[slug]]):
                n = min(512, len(f.points))
                k[2 * b + offset, :n] = torch.tensor(
                    (f.points[:n] - np.array(f.size) / 2) / (max(f.size) / 2),
                    device=self.device,
                    dtype=torch.float32,
                )
                d[2 * b + offset, :n] = f.descriptors[:n]
        pairs = self.strong(k, d)[0].cpu().numpy()
        evidence = []
        for b, slug in enumerate(ids):
            matches = pairs[pairs[:, 0] == b, 1:]
            matches = matches[
                (matches[:, 0] < len(query.points))
                & (matches[:, 1] < len(refs[slug].points))
            ]
            evidence.append({"id": slug, **self.geometry(query, refs[slug], matches)})
        return evidence

    def ranked_coarse(self, q, ids):
        if not ids or len(q.points) < 8:
            return []
        # Reuse batched MNN implementation with the independent ALIKED gallery.
        old_refs, old_descriptors = self.refs, self.reference_descriptors
        self.refs, self.reference_descriptors = (
            self.verification,
            self.aliked_descriptors,
        )
        try:
            return self.coarse_many(q, ids)
        finally:
            self.refs, self.reference_descriptors = old_refs, old_descriptors

    strong_choice = staticmethod(select_strong)

    def refine(self, image, observations, found, started):
        for item in observations:
            item["semanticCandidates"] = self.semantic_rankings.get(
                tuple(item["box"]), []
            )
            if item["id"]:
                evidence = next(
                    (e for e in item["evidence"] if e["id"] == item["id"]), {}
                )
                # A common neck crest cannot identify the label beneath it.
                if evidence.get("labelInliers", 0) < 12:
                    item["id"] = None
                    item["rejection"] = "insufficient-label-evidence"
        uncertain = [o for o in observations if not o["id"] and "retrieval_q" in o]
        uncertain.sort(
            key=lambda o: max((e["inliers"] for e in o["evidence"]), default=0),
            reverse=True,
        )
        if self.semantic and self.semantic_scope == "selected":
            # Validate tentative positives even if no rescue time remains.
            selected = (
                [o for o in observations if o["id"]] if self.semantic_veto else []
            )
            if time.perf_counter() - started < self.budget_seconds:
                selected += uncertain[: self.rescue_limit]
            boxes = [o["box"] for o in selected]
            self.semantic_rankings = dict(
                zip(map(tuple, boxes), self.semantic.rank(image, boxes))
            )
            for item in selected:
                item["semanticCandidates"] = self.semantic_rankings[tuple(item["box"])]
        if self.semantic and self.semantic_veto:
            for item in observations:
                if item["id"] and item["id"] not in item.get("semanticCandidates", []):
                    item["id"] = None
                    item["rejection"] = "semantic-disagreement"
        ocr_count = 0
        if len(uncertain) > self.rescue_limit:
            self.scan_warnings.append(
                "Часть неуверенных совпадений не уточнялась. Попробуйте снять полку крупнее."
            )
        for item in uncertain[: self.rescue_limit]:
            if time.perf_counter() - started >= self.budget_seconds:
                self.scan_warnings.append(
                    "Достигнут лимит времени уточнения. Неуверенные совпадения не показаны."
                )
                break
            q = item["retrieval_q"]
            ids = list(
                dict.fromkeys(
                    item.get("semanticCandidates", [])[:12]
                    + item["ranking"][:64]
                    + found
                )
            )
            coarse = sorted(self.ranked_coarse(q, ids), key=lambda e: -e["inliers"])
            if not coarse or coarse[0]["inliers"] < 8:
                continue
            ids = list(
                dict.fromkeys(
                    [e["id"] for e in coarse[:4]]
                    + [e["id"] for e in item["evidence"][:2]]
                )
            )[:6]
            evidence = self.strong_many(q, ids, self.verification)
            choice = self.strong_choice(evidence)
            stage = "strong-full"
            if (
                not choice
                and self.use_labels
                and time.perf_counter() - started < self.budget_seconds
            ):
                x0, y0, x1, y1 = item["box"]
                label_box = [x0, y0 + (y1 - y0) * 0.25, x1, y1]
                label_q = self.extract(image, label_box, self.retriever, 0.2)
                views = torch.stack(
                    [self.vlad(label_q, False), self.vlad(label_q, True)]
                )
                scores = (self.label_vectors * views).sum(-1).amax(-1)
                label_ids = [
                    self.ids[i] for i in scores.topk(12).indices.cpu().tolist()
                ]
                ids = list(dict.fromkeys(ids[:3] + label_ids[:3]))
                label_evidence = self.strong_many(label_q, ids, self.labels)
                label_choice = self.strong_choice(label_evidence)
                if label_choice:
                    choice = label_choice
                    evidence = label_evidence
                    stage = "strong-label"
            item["rescueEvidence"] = sorted(evidence, key=lambda e: -e["inliers"])
            item["rescueStage"] = stage
            if (
                self.ocr
                and ocr_count < 3
                and time.perf_counter() - started < self.budget_seconds
            ):
                # OCR is a veto or tie breaker between geometrically eligible candidates.
                ordered = sorted(evidence, key=lambda e: -e["inliers"])
                eligible = [e["id"] for e in ordered if self.strong_choice([e])]
                if choice or len(eligible) > 1:
                    text = self.ocr.read(image, item["box"])
                    ocr_count += 1
                    item["ocr"] = text
                    if choice and self.ocr.conflicts(text, choice):
                        choice = None
                    elif not choice:
                        candidate = self.ocr.choose(text, eligible)
                        if ordered and candidate == ordered[0]["id"]:
                            choice = candidate
            if (
                choice
                and self.semantic
                and self.semantic_veto
                and choice not in item.get("semanticCandidates", [])[:1]
            ):
                choice = None
                item["rejection"] = "semantic-disagreement"
            if choice:
                item["id"] = choice
        if self.ocr:
            for item in observations:
                if (
                    not item["id"]
                    or ocr_count >= 3
                    or time.perf_counter() - started >= self.budget_seconds
                ):
                    continue
                text = self.ocr.read(image, item["box"])
                ocr_count += 1
                if self.ocr.conflicts(text, item["id"]):
                    item["id"] = None
                    item["rejection"] = "ocr-conflict"
