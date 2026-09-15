"""Метрики поиска.

ТЗ просит «F1 для топ-1 и топ-5». Для задачи «одно фото — одно вино» определяем так:
сервис может отказаться отвечать, если уверенность ниже порога τ (экран «не найдено»).

- precision@k = верных ответов / данных ответов,
- recall@k    = верных ответов / всех запросов с вином из каталога,
- F1@k        = гармоническое среднее;

«верный» для k=1 — правильный top-1, для k=5 — правильное вино есть в top-5.
Без порога (отвечаем всегда) precision = recall = F1 = accuracy.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def f1_with_abstention(correct: np.ndarray, answered: np.ndarray) -> dict[str, float]:
    correct, answered = np.asarray(correct, bool), np.asarray(answered, bool)
    hits = int((correct & answered).sum())
    precision = hits / answered.sum() if answered.any() else 0.0
    recall = hits / len(correct) if len(correct) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "answered_share": float(answered.mean()) if len(answered) else 0.0}


def best_threshold(correct: np.ndarray, confidence: np.ndarray) -> dict[str, float]:
    """Порог уверенности, максимизирующий F1 (перебор по наблюдаемым значениям)."""
    correct, confidence = np.asarray(correct, bool), np.asarray(confidence, float)
    best = {"threshold": float("-inf"), **f1_with_abstention(correct, np.ones_like(correct))}
    for threshold in np.unique(confidence):
        candidate = f1_with_abstention(correct, confidence >= threshold)
        if candidate["f1"] > best["f1"]:
            best = {"threshold": float(threshold), **candidate}
    return best


def auroc(positive: np.ndarray, negative: np.ndarray) -> float:
    """Вероятность, что случайный позитив получил скор выше случайного негатива (Манн — Уитни)."""
    positive, negative = np.asarray(positive, float), np.asarray(negative, float)
    if not len(positive) or not len(negative):
        return float("nan")
    combined = np.concatenate([positive, negative])
    order = combined.argsort(kind="mergesort")
    ranks = np.empty(len(combined))
    ranks[order] = np.arange(1, len(combined) + 1)
    for value in np.unique(combined):  # средний ранг для одинаковых значений
        tied = combined == value
        ranks[tied] = ranks[tied].mean()
    return float((ranks[: len(positive)].sum() - len(positive) * (len(positive) + 1) / 2) / (len(positive) * len(negative)))


def open_set_at_threshold(
    positive_correct: np.ndarray, positive_confidence: np.ndarray, negative_confidence: np.ndarray, threshold: float
) -> dict[str, float]:
    """Открытое множество: вино из каталога верно, если ответ дан и top-1 правильный; вино вне
    каталога верно, если сервис отказался. Ответ даётся при уверенности ≥ порога."""
    positive_correct = np.asarray(positive_correct, bool)
    answered = np.asarray(positive_confidence) >= threshold
    rejected = np.asarray(negative_confidence) < threshold
    total = len(positive_correct) + len(rejected)
    return {
        "threshold": threshold,
        "open_set_accuracy": float(((positive_correct & answered).sum() + rejected.sum()) / total) if total else 0.0,
        "in_catalog_top1_answered": float((positive_correct & answered).mean()) if len(answered) else 0.0,
        "in_catalog_rejected": float((~answered).mean()) if len(answered) else 0.0,
        "out_of_catalog_rejected": float(rejected.mean()) if len(rejected) else 0.0,
    }


def retrieval_summary(ranks: Sequence[int | None], confidence: Sequence[float]) -> dict[str, float]:
    """ranks — позиция верного вина в выдаче (1 — первое место), None — не попало в выдачу."""
    rank_array = np.array([r if r is not None else np.inf for r in ranks], dtype=float)
    top1, top5 = rank_array <= 1, rank_array <= 5
    confidence = np.asarray(confidence, float)
    return {
        "queries": len(rank_array),
        "top1_accuracy": float(top1.mean()) if len(rank_array) else 0.0,
        "top5_accuracy": float(top5.mean()) if len(rank_array) else 0.0,
        "mrr_at_10": float(np.where(rank_array <= 10, 1 / rank_array, 0).mean()) if len(rank_array) else 0.0,
        "f1_at_1_best": best_threshold(top1, confidence),
        "f1_at_5_best": best_threshold(top5, confidence),
    }
