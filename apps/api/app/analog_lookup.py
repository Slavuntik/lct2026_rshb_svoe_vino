"""Общий вызов «стиль -> вина-аналоги» (contracts/rag-interface.md:
analog_for_style + маппинг Candidate -> AnalogsWineItem). Единственное место
этой логики: используется и POST /v1/analogs (routers/analogs.py), и
фолбэком POST /v1/scan/resolve на пустых matches (routers/scan.py,
agents/B7-foreign-analogs.md, п.1 "НЕ дублируй логику, вынеси общий вызов").
Резолвер стиля (retriever.resolve_style) — тоже общий, но вызывается
отдельно на стороне роутера: разные роутеры добывают style-query по-разному
(тело запроса /analogs целиком vs. распознанный токен сорта/стиля у scan).
"""
from __future__ import annotations

from .rag.interface import Filters, Retriever
from .schemas import AnalogsWineItem


def wines_for_style(
    retriever: Retriever, style_slug: str, *, filters: Filters | None = None, top_k: int = 12,
) -> list[AnalogsWineItem]:
    candidates = retriever.analog_for_style(style_slug, filters=filters, top_k=top_k)
    return [
        # meta для kind=wine — {"source": {...}, "derived": {...}} (v0.3).
        AnalogsWineItem(
            wine_id=c.id, name=c.meta["source"].get("name", c.id),
            # Хотфикс оркестратора: честный None при отсутствующей винодельне
            # (без default) — AnalogsWineItem.winery_name теперь Optional,
            # см. app/schemas.py. .get(..., "") здесь ранее подменял бы None
            # РЕАЛЬНО отсутствующей винодельни на "" молча.
            winery_name=c.meta["source"].get("winery_name"),
            region_name=c.meta["source"].get("region_name", ""),
        )
        for c in candidates
    ]
