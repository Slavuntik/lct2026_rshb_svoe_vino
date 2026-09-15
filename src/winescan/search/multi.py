"""Слой 3: поиск по нескольким индексам (модели и виды могут различаться), скоры вин складываются."""

from __future__ import annotations

import numpy as np
from PIL import Image

from winescan.config import get_paths
from winescan.search.index import VectorIndex, top_k
from winescan.vision.embedder import ImageEmbedder
from winescan.vision.preprocess import Box, query_view


class MultiIndexSearcher:
    def __init__(self, index_names: list[str], weights: list[float], device: str | None = None):
        if len(index_names) != len(weights):
            raise ValueError("число весов не совпадает с числом индексов")
        paths = get_paths()
        self.index_names = list(index_names)
        self.indexes = [VectorIndex.load(paths.artifacts_dir / "index" / name) for name in index_names]
        self.weights = list(weights)
        self.wine_slugs = self.indexes[0].wine_slugs
        if any(index.wine_slugs != self.wine_slugs for index in self.indexes):
            raise ValueError("индексы построены по разным наборам вин — пересоберите их")
        self.embedders: dict[str, ImageEmbedder] = {}
        for index in self.indexes:
            model_id = index.meta["model_id"]
            if model_id not in self.embedders:
                self.embedders[model_id] = ImageEmbedder(model_id, device=device)

    @property
    def device(self) -> str:
        return next(iter(self.embedders.values())).device

    def embed_views(self, images: list[Image.Image], boxes: list[Box | None], batch_size: int = 32) -> list[np.ndarray]:
        """Эмбеддинги кропов для каждого индекса (его модель и вид): список массивов (n, dim)."""
        vectors = []
        for index in self.indexes:
            view = index.meta.get("view_name", "full")
            views = [query_view(image, box, view) for image, box in zip(images, boxes)]
            vectors.append(self.embedders[index.meta["model_id"]].embed(views, batch_size=batch_size))
        return vectors

    def wine_scores(self, vectors: list[np.ndarray]) -> np.ndarray:
        """Взвешенная сумма скоров вин по индексам: (n, число вин)."""
        total = np.zeros((len(vectors[0]), len(self.wine_slugs)), dtype=np.float32)
        for index, weight, index_vectors in zip(self.indexes, self.weights, vectors):
            total += weight * index.wine_scores(index_vectors.astype(np.float32))
        return total

    def search(self, images: list[Image.Image], boxes: list[Box | None], k: int) -> tuple[list[list[str]], np.ndarray]:
        return top_k(self.wine_scores(self.embed_views(images, boxes)), self.wine_slugs, k)
