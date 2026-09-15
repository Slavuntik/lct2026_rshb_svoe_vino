"""Слой 3: слияние сигналов по кандидатам и решение «найдено / не найдено».

Итоговый скор кандидата = визуальный скор (сумма косинусов индексов)
                        + local_weight × бонус за SIFT-совпадения (0..1)
                        + text_weight × сходство текста этикетки.
Визуальные скоры кандидатов отличаются на сотые, поэтому веса подбираются на валидации
(winescan.eval.local_rerank_eval, winescan.eval.rerank_sweep).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from winescan.search.text_match import LabelText, text_score

LOCAL_SATURATION = 150


@dataclass(frozen=True)
class Candidate:
    slug: str
    score: float
    visual_score: float
    local_inliers: int = 0
    text_score: float = 0.0


def local_bonus(inlier_count: int, saturation: int = LOCAL_SATURATION) -> float:
    """Нормированный бонус за SIFT-inliers в [0; 1]: рост замедляется, после saturation — насыщение."""
    return math.log1p(min(inlier_count, saturation)) / math.log1p(saturation)


def fuse(
    slugs: list[str],
    visual_scores: list[float],
    local_inliers: dict[str, int] | None = None,
    text_scores: dict[str, float] | None = None,
    local_weight: float = 0.0,
    text_weight: float = 0.0,
) -> list[Candidate]:
    candidates = []
    for slug, visual in zip(slugs, visual_scores):
        inliers = (local_inliers or {}).get(slug, 0)
        textual = (text_scores or {}).get(slug, 0.0)
        score = float(visual) + local_weight * local_bonus(inliers) + text_weight * textual
        candidates.append(Candidate(slug, score, float(visual), inliers, textual))
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def rerank(
    slugs: list[str], visual_scores: list[float], cards: dict[str, dict], label: LabelText, text_weight: float
) -> list[Candidate]:
    """Переранжирование только по тексту этикетки."""
    text_scores = {slug: text_score(cards[slug], label) for slug in slugs if slug in cards}
    return fuse(slugs, visual_scores, text_scores=text_scores, text_weight=text_weight)


@dataclass(frozen=True)
class Decision:
    status: str  # found | not_found
    reason: str


def decide(candidates: list[Candidate], min_visual_score: float | None = None, min_margin: float | None = None) -> Decision:
    """«Не найдено», если лучший кандидат визуально слишком далёк (вина, вероятно, нет в каталоге)
    или отрыв от второго слишком мал, чтобы показывать одну карточку."""
    if not candidates:
        return Decision("not_found", "нет кандидатов")
    best = candidates[0]
    if min_visual_score is not None and best.visual_score < min_visual_score:
        return Decision("not_found", f"визуальный скор {best.visual_score:.3f} < {min_visual_score}")
    margin = best.score - candidates[1].score if len(candidates) > 1 else math.inf
    if min_margin is not None and margin < min_margin:
        return Decision("not_found", f"отрыв {margin:.4f} < {min_margin}")
    return Decision("found", "")
