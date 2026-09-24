"""Слой 3: выбор рамки по результату поиска — общая формула для сервиса и офлайн-оценки.

Детектор предлагает до K рамок; по каждой считается поиск. Рамка выбирается по
    rule.prior · log(prior / prior_max) + rule.top1 · top1 + rule.margin · отрыв,
где prior — априорный вес рамки (vision.detector.rank_packages), top1 и отрыв — скор лучшего
вина и его отрыв от второго по этой рамке. Правило {"prior": 1, "top1": 0, "margin": 0} —
прежнее поведение (первая рамка по весу). Веса подбираются в ``winescan.eval.offline box-selection``.
"""

from __future__ import annotations

import math

PRIOR_ONLY = {"prior": 1.0, "top1": 0.0, "margin": 0.0}


def choose_box(priors: list[float], top1s: list[float], margins: list[float], rule: dict = PRIOR_ONLY) -> int:
    if not priors:
        raise ValueError("нет рамок-кандидатов")
    prior_max = max(priors) or 1.0
    values = [
        rule.get("prior", 0.0) * math.log(max(prior, 1e-9) / prior_max)
        + rule.get("top1", 0.0) * top1
        + rule.get("margin", 0.0) * margin
        for prior, top1, margin in zip(priors, top1s, margins)
    ]
    return max(range(len(values)), key=values.__getitem__)
