"""Единая сборка карточки вина — используется И `GET /wines/{id}`, И
`card` в rich-режиме `/scan/photo` (contracts/image-scan.md v0.4.1: "card —
ровно тело ответа GET /wines/{id}"). Один построитель — чтобы эти два места
не могли снова разойтись по форме (ровно тот баг, который нашёл C и
зафиксировал v0.4.1: card в CV-пайплайне не нёс `similar`).
"""
from __future__ import annotations

from .interface import Retriever


def build_wine_card(retriever: Retriever, wine_id: str, *, similar_top_k: int = 6) -> dict | None:
    """{wine_id, source, derived, source_url, similar} — как GET /wines/{id},
    буквально. None, если wine_id не резолвится в вино (или вовсе неизвестен
    ретриверу) — вызывающая сторона решает, что это значит у себя (404 у
    /wines/{id}, card=None у /scan/photo)."""
    candidate = retriever.get_by_id(wine_id)
    if candidate is None or candidate.kind != "wine":
        return None
    similar = [c.id for c in retriever.similar(wine_id, top_k=similar_top_k)]
    return {
        "wine_id": wine_id,
        "source": candidate.meta["source"],
        "derived": candidate.meta["derived"],
        "source_url": candidate.url,
        "similar": similar,
    }
