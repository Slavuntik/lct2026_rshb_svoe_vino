"""Слой 3: векторный индекс эталонов, точный поиск по косинусу.

На 2–5 тыс. вин точный перебор в numpy занимает миллисекунды, приближённый индекс не нужен.
У одного вина может быть несколько векторов (несколько ракурсов эталона): скор вина —
максимум по его векторам.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class VectorIndex:
    slugs: list[str]  # по строке на вектор, строки одного вина идут подряд
    vectors: np.ndarray  # (n, d), L2-нормированные
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.slugs) != len(self.vectors):
            raise ValueError("slugs и vectors разной длины")
        starts = [0] + [i for i in range(1, len(self.slugs)) if self.slugs[i] != self.slugs[i - 1]]
        self._starts = np.array(starts)
        self.wine_slugs = [self.slugs[i] for i in starts]
        if len(set(self.wine_slugs)) != len(self.wine_slugs):
            raise ValueError("строки одного вина должны идти подряд")

    def wine_scores(self, queries: np.ndarray) -> np.ndarray:
        """(m, число вин): косинус запроса с лучшим вектором каждого вина."""
        return np.maximum.reduceat(queries @ self.vectors.T, self._starts, axis=1)

    def search(self, queries: np.ndarray, k: int = 5) -> tuple[list[list[str]], np.ndarray]:
        scores = self.wine_scores(queries)
        k = min(k, scores.shape[1])
        order = np.argsort(-scores, axis=1)[:, :k]
        return [[self.wine_slugs[i] for i in row] for row in order], np.take_along_axis(scores, order, axis=1)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        (directory / "slugs.json").write_text(json.dumps(self.slugs, ensure_ascii=False), encoding="utf-8")
        (directory / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> VectorIndex:
        return cls(
            slugs=json.loads((directory / "slugs.json").read_text(encoding="utf-8")),
            vectors=np.load(directory / "vectors.npy"),
            meta=json.loads((directory / "meta.json").read_text(encoding="utf-8")),
        )
