"""Fuzzy-резолверы: resolve_label (этикетка -> вино) и resolve_style
(«люблю Просекко» -> slug эталонного стиля). Оба — rapidfuzz, НЕ векторный
поиск (см. контракт).
"""
from __future__ import annotations

from rapidfuzz import fuzz, process, utils

from rag import config
from rag.types import Candidate


def resolve_label(
    query: str,
    labels: list[dict],
    *,
    hints: dict | None = None,
    limit: int = 5,
    garbage_cutoff: int = config.LABEL_GARBAGE_CUTOFF,
    confident_cutoff: int = config.LABEL_CONFIDENT_CUTOFF,
) -> list[Candidate]:
    query = (query or "").strip()
    if not query or not labels:
        return []

    choices = [lab["search_text"] for lab in labels]
    matches = process.extract(
        query, choices, scorer=fuzz.token_set_ratio, processor=utils.default_process, limit=limit
    )

    results: list[Candidate] = []
    for _choice, score, idx in matches:
        if score < garbage_cutoff:
            continue
        lab = labels[idx]
        results.append(
            Candidate(
                id=lab["id"],
                kind="wine",
                score=round(score / 100.0, 4),
                text=lab["text"],
                url=lab["url"],
                meta={
                    "match_score": score,
                    "low_confidence": score < confident_cutoff,
                    "name": lab["name"],
                    "winery_name": lab["winery_name"],
                },
            )
        )
    return results


def resolve_style(
    query: str,
    styles: list[dict],
    *,
    garbage_cutoff: int = config.STYLE_GARBAGE_CUTOFF,
) -> dict | None:
    query = (query or "").strip()
    if not query or not styles:
        return None

    choices: list[str] = []
    owners: list[dict] = []
    for s in styles:
        choices.append(s["name"])
        owners.append(s)
        choices.append(s["slug"].replace("-", " "))
        owners.append(s)

    match = process.extractOne(query, choices, scorer=fuzz.token_set_ratio, processor=utils.default_process)
    if not match:
        return None
    _choice, score, idx = match
    if score < garbage_cutoff:
        return None
    s = owners[idx]
    return {"slug": s["slug"], "name": s["name"], "country": s.get("country")}
