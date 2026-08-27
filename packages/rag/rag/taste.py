"""candidates_for_taste: колода для свайп-дегустации (v0.2.3).

Чисто метаданный подбор — как analog_for_style, без эмбеддингов/Qdrant:
жадно набираем колоду, на каждом шаге предпочитая вино, чьи цвет/регион/
топ-стиль ещё не встречались в уже собранной колоде — дёшево даёт
разнообразие без реального кластеринга по сенсорному вектору. Порядок
кандидатов до жадного отбора — детерминированно перемешан seed'ом по дате
(«детерминированной случайности достаточно», см. контракт v0.2.3): в
течение дня колода стабильна, на следующий день — новая.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from rag.meta import public_meta
from rag.types import Candidate


def _diversity_key(payload: dict, seen_colors: set, seen_regions: set, seen_styles: set) -> int:
    f = payload.get("filters", {}) or {}
    color = f.get("color")
    region = f.get("region")
    styles = f.get("reference_style_matches") or []
    style = styles[0] if styles else None
    return (
        (color is not None and color not in seen_colors)
        + (region is not None and region not in seen_regions)
        + (style is not None and style not in seen_styles)
    )


def build_taste_deck(
    wine_payloads: list[dict],
    exclude_ids: list[str] | None,
    limit: int = 20,
    *,
    seed: int | None = None,
) -> list[Candidate]:
    exclude = set(exclude_ids or [])
    pool = [w for w in wine_payloads if w.get("id") not in exclude]
    if not pool:
        return []

    if seed is None:
        seed = int(datetime.now(timezone.utc).strftime("%Y%m%d"))
    rng = random.Random(seed)
    remaining = pool[:]
    rng.shuffle(remaining)

    seen_colors: set = set()
    seen_regions: set = set()
    seen_styles: set = set()
    deck: list[dict] = []

    while remaining and len(deck) < limit:
        remaining.sort(key=lambda w: _diversity_key(w, seen_colors, seen_regions, seen_styles), reverse=True)
        chosen = remaining.pop(0)
        deck.append(chosen)
        f = chosen.get("filters", {}) or {}
        if f.get("color") is not None:
            seen_colors.add(f["color"])
        if f.get("region") is not None:
            seen_regions.add(f["region"])
        styles = f.get("reference_style_matches") or []
        if styles:
            seen_styles.add(styles[0])

    return [
        Candidate(
            id=w["id"],
            kind=w.get("kind", "wine"),
            score=1.0,
            text=w.get("text", ""),
            url=w.get("url", ""),
            meta=public_meta(w),
        )
        for w in deck
    ]
