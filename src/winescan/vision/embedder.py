"""Слой 2: визуальные эмбеддинги (SigLIP 2 через transformers)."""

from __future__ import annotations

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

DEFAULT_EMBEDDER = "google/siglip2-base-patch16-224"


def default_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


class ImageEmbedder:
    def __init__(self, model_id: str = DEFAULT_EMBEDDER, device: str | None = None):
        self.model_id = model_id
        self.device = device or default_device()
        self.dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModel.from_pretrained(model_id, torch_dtype=self.dtype).to(self.device).eval()

    @torch.inference_mode()
    def embed(self, images: list[Image.Image], batch_size: int = 32) -> np.ndarray:
        """L2-нормированные эмбеддинги float32, по строке на изображение."""
        chunks = []
        for start in range(0, len(images), batch_size):
            inputs = self.processor(images=images[start : start + batch_size], return_tensors="pt")
            inputs = {
                name: tensor.to(self.device, self.dtype if tensor.is_floating_point() else tensor.dtype)
                for name, tensor in inputs.items()
            }
            features = self.model.get_image_features(**inputs)
            # transformers 5 возвращает BaseModelOutputWithPooling, 4.x — сразу тензор
            features = getattr(features, "pooler_output", features)
            chunks.append(torch.nn.functional.normalize(features.float(), dim=-1).cpu().numpy())
        return np.concatenate(chunks).astype(np.float32)
