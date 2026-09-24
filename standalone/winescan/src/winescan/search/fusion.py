"""Слой 3: обученное слияние сигналов по кандидатам (вместо ручных весов 0,15 и 0,02).

Для каждого кандидата из визуального top-K считаются признаки (визуальный скор, отрыв от
лучшего, признаки проверки из search.verify, согласие полей этикетки из search.fields) и
логистическая регрессия оценивает «это то самое вино». Ранжирование — по её логиту;
уверенность — вероятность лучшего кандидата и отрыв логитов (для полос решения).

Модель — несколько весов в JSON (configs/fusion_*.json): воспроизводима и читается глазами.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FEATURES = (
    "visual",  # взвешенная сумма косинусов индексов
    "gap",  # visual − visual лучшего кандидата (≤ 0)
    "log_inliers",
    "inlier_ratio",
    "coverage",
    "ncc",
    "color_distance",
    "overlap",
    "field_score",  # согласие полей этикетки (0, если модель не вызывалась)
)


def candidate_features(visual: float, best_visual: float, verification: dict | None, field_score: float = 0.0) -> dict:
    verification = verification or {}
    return {
        "visual": visual,
        "gap": visual - best_visual,
        "log_inliers": math.log1p(verification.get("inliers", 0)),
        "inlier_ratio": verification.get("inlier_ratio", 0.0),
        "coverage": verification.get("coverage", 0.0),
        "ncc": verification.get("ncc", 0.0),
        "color_distance": verification.get("color_distance", 1.0),
        "overlap": verification.get("overlap", 0.0),
        "field_score": field_score,
    }


@dataclass
class FusionModel:
    weights: dict[str, float]
    bias: float
    means: dict[str, float]
    stds: dict[str, float]
    meta: dict = field(default_factory=dict)

    def logit(self, features: dict) -> float:
        total = self.bias
        for name, weight in self.weights.items():
            std = self.stds.get(name) or 1.0
            total += weight * (features.get(name, 0.0) - self.means.get(name, 0.0)) / std
        return total

    def rank(self, candidates: list[dict]) -> list[tuple[float, dict]]:
        """candidates: [{"slug": ..., "features": {...}}] -> [(логит, кандидат)] по убыванию."""
        return sorted(((self.logit(c["features"]), c) for c in candidates), key=lambda pair: pair[0], reverse=True)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"weights": self.weights, "bias": self.bias, "means": self.means, "stds": self.stds, "meta": self.meta}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> FusionModel:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data["weights"], data["bias"], data["means"], data["stds"], data.get("meta", {}))


def train(rows: list[dict], feature_names: tuple[str, ...] = FEATURES, c: float = 1.0) -> FusionModel:
    """Логистическая регрессия по строкам {"features": {...}, "label": bool} (стандартизация внутри)."""
    from sklearn.linear_model import LogisticRegression

    matrix = np.array([[row["features"].get(name, 0.0) for name in feature_names] for row in rows], dtype=np.float64)
    labels = np.array([bool(row["label"]) for row in rows])
    means, stds = matrix.mean(axis=0), matrix.std(axis=0)
    stds[stds == 0] = 1.0
    model = LogisticRegression(C=c, max_iter=1000, class_weight="balanced")
    model.fit((matrix - means) / stds, labels)
    return FusionModel(
        weights={name: float(w) for name, w in zip(feature_names, model.coef_[0])},
        bias=float(model.intercept_[0]),
        means={name: float(m) for name, m in zip(feature_names, means)},
        stds={name: float(s) for name, s in zip(feature_names, stds)},
        meta={"rows": len(rows), "positives": int(labels.sum()), "C": c},
    )


def top1_accuracy(model: FusionModel, queries: dict[str, list[dict]]) -> float:
    """queries: query_id -> кандидаты с features и label; доля запросов, где лучший по логиту верен."""
    hits = [model.rank(candidates)[0][1]["label"] for candidates in queries.values() if any(c["label"] for c in candidates)]
    return float(np.mean(hits)) if hits else 0.0
