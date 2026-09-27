"""Standalone SigLIP2 retrieval from full/label galleries; no WineScan imports."""

import json
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image
import torch


class SemanticRetriever:
    def __init__(self, directory, device):
        from transformers import AutoImageProcessor, AutoModel

        self.directory = Path(directory)
        manifest = json.loads((self.directory / "manifest.json").read_text())
        for name, digest in manifest["hashes"].items():
            path = (self.directory / name).resolve()
            if (
                not path.is_relative_to(self.directory.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("Invalid semantic asset")
        self.device = device
        self.dtype = torch.float32 if device == "cpu" else torch.float16
        self.processor = AutoImageProcessor.from_pretrained(
            directory / "encoder", local_files_only=True
        )
        self.model = (
            AutoModel.from_pretrained(
                directory / "encoder", local_files_only=True, dtype=self.dtype
            )
            .to(device)
            .eval()
        )
        self.galleries = []
        for view in ["full", "label"]:
            path = directory / view
            slugs = json.loads((path / "slugs.json").read_text())
            starts = [0] + [i for i in range(1, len(slugs)) if slugs[i] != slugs[i - 1]]
            ids = [slugs[i] for i in starts]
            if self.galleries and ids != self.ids:
                raise ValueError("Semantic gallery order differs")
            self.ids = ids
            self.galleries.append(
                (np.load(path / "vectors.npy", allow_pickle=False), np.array(starts))
            )

    @staticmethod
    def view(image, box, label):
        x0, y0, x1, y1 = [
            v * (image.width if i % 2 == 0 else image.height) for i, v in enumerate(box)
        ]
        mx, my = (x1 - x0) * 0.03, (y1 - y0) * 0.03
        crop = image.crop(
            (
                round(max(0, x0 - mx)),
                round(max(0, y0 - my)),
                round(min(image.width, x1 + mx)),
                round(min(image.height, y1 + my)),
            )
        )
        if label and crop.height / max(crop.width, 1) >= 1.8:
            crop = crop.crop(
                (0, round(crop.height * 0.4), crop.width, round(crop.height * 0.97))
            )
        side = round(max(crop.size) * 1.08)
        square = Image.new("RGB", (side, side), "white")
        square.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
        return square

    @torch.inference_mode()
    def rank(self, image, boxes):
        if not boxes:
            return []
        total = np.zeros((len(boxes), len(self.ids)), dtype=np.float32)
        for label, (vectors, starts) in enumerate(self.galleries):
            chunks = []
            for start in range(0, len(boxes), 16):
                images = [self.view(image, b, label) for b in boxes[start : start + 16]]
                inputs = self.processor(images=images, return_tensors="pt")
                inputs = {
                    k: v.to(
                        self.device, self.dtype if v.is_floating_point() else v.dtype
                    )
                    for k, v in inputs.items()
                }
                output = self.model.get_image_features(**inputs)
                features = getattr(output, "pooler_output", output)
                chunks.append(
                    torch.nn.functional.normalize(features.float(), dim=-1)
                    .cpu()
                    .numpy()
                )
            total += (
                np.maximum.reduceat(np.concatenate(chunks) @ vectors.T, starts, axis=1)
                * 0.5
            )
        return [
            [self.ids[i] for i in np.argsort(-scores, kind="stable")[:24]]
            for scores in total
        ]
