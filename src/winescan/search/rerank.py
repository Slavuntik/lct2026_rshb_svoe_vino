"""Слой 3: переранжирование визуальных кандидатов по тексту этикетки.

Итоговый скор = косинус SigLIP + text_weight × text_score. Косинусы кандидатов обычно
отличаются на сотые, поэтому вес текста подбирается на валидации (winescan.eval.rerank_sweep).
"""

from __future__ import annotations

from dataclasses import dataclass

from winescan.search.text_match import LabelText, text_score


@dataclass(frozen=True)
class Candidate:
    slug: str
    score: float
    visual_score: float
    text_score: float


def rerank(
    slugs: list[str], visual_scores: list[float], cards: dict[str, dict], label: LabelText, text_weight: float
) -> list[Candidate]:
    candidates = []
    for slug, visual in zip(slugs, visual_scores):
        textual = text_score(cards[slug], label) if slug in cards else 0.0
        candidates.append(Candidate(slug, float(visual) + text_weight * textual, float(visual), textual))
    return sorted(candidates, key=lambda c: c.score, reverse=True)
