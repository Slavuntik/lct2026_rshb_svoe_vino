"""Слой 1–3: обучаемый выбор рамки целевой упаковки среди K рамок детектора.

Ручная формула (vision.detector.rank_packages) выбирает верную бутылку в 90% кадров synth_v1 и
82% synth_v2; правило «уверенность поиска» не помогло (соседи на полке тоже есть в каталоге).
Бустинг по геометрии рамки, уверенности детектора и результату поиска по рамке поднял на
проверочных половинах верную рамку до 0,93 / 0,86 и top-1 на +1,7 / +2,6 п.п. (eval.train_box_ranker).

Признаки считаются одной функцией и в сервисе, и в офлайн-оценке.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FEATURES = ("det_score", "log_prior", "rank", "area", "centrality", "cy", "aspect", "width", "top1", "margin", "top1_gap")


def box_features(
    boxes: list[tuple[float, float, float, float]],
    det_scores: list[float],
    priors: list[float],
    image_size: tuple[int, int],
    top1s: list[float],
    margins: list[float],
) -> list[list[float]]:
    """Признаки рамок-кандидатов в порядке априорного веса (rank 0 — первая)."""
    width, height = image_size
    prior_max = max(priors) or 1e-9
    best_top1 = max(top1s)
    rows = []
    for rank, (box, det_score, prior, top1, margin) in enumerate(zip(boxes, det_scores, priors, top1s, margins)):
        x0, y0, x1, y1 = box
        rows.append([
            det_score,
            math.log(max(prior, 1e-9) / prior_max),
            rank,
            (x1 - x0) * (y1 - y0) / (width * height),
            1 - min(1.0, abs((x0 + x1) / 2 / width - 0.5) * 2),
            (y0 + y1) / 2 / height,
            (y1 - y0) / max(x1 - x0, 1.0),
            (x1 - x0) / width,
            top1,
            margin,
            top1 - best_top1,
        ])  # fmt: skip
    return rows


@dataclass
class BoxRanker:
    model: object  # sklearn-классификатор с predict_proba
    meta: dict = field(default_factory=dict)

    def choose(self, rows: list[list[float]]) -> int:
        probabilities = self.model.predict_proba(np.asarray(rows, dtype=np.float64))[:, 1]
        return int(np.argmax(probabilities))

    def save(self, path: Path) -> None:
        import joblib

        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "meta": self.meta, "features": FEATURES}, path)

    @classmethod
    def load(cls, path: Path) -> BoxRanker:
        import joblib

        payload = joblib.load(path)
        if tuple(payload["features"]) != FEATURES:
            raise ValueError(f"{path}: модель обучена на других признаках: {payload['features']}")
        return cls(payload["model"], payload["meta"])


def train_box_ranker(rows: list[list[float]], labels: list[bool], seed: int = 0) -> object:
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=seed)
    return model.fit(np.asarray(rows, dtype=np.float64), np.asarray(labels, dtype=bool))
