"""POST /analogs — детерминированный путь «аналог импортного» (v0.2):
resolve_style -> analog_for_style, без обращения к LLM. list_reference_styles
— часть contracts/rag-interface.md с v0.2.1 (было предложением агента B).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_retriever_dep
from ..errors import ApiError
from ..rag.interface import Filters, Retriever
from ..schemas import AnalogsRequest, AnalogsResponse, AnalogsStyle, AnalogsWineItem
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/analogs", tags=["analogs"])


@router.post("", response_model=AnalogsResponse)
def analogs(
    body: AnalogsRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
) -> AnalogsResponse:
    style = retriever.resolve_style(body.query)
    if style is None:
        top_styles = retriever.list_reference_styles(top_n=5)
        hint = ", ".join(s["name"] for s in top_styles)
        message = "Не удалось распознать стиль по описанию"
        if hint:
            message += f". Популярные стили: {hint}"
        raise ApiError(404, "not_found", message)

    filters = Filters(
        region=body.filters.region if body.filters else None,
        sugar=body.filters.sugar if body.filters else None,
    )
    candidates = retriever.analog_for_style(style["slug"], filters=filters, top_k=12)
    wines = [
        # meta для kind=wine — {"source": {...}, "derived": {...}} (v0.3).
        AnalogsWineItem(
            wine_id=c.id, name=c.meta["source"].get("name", c.id),
            winery_name=c.meta["source"].get("winery_name", ""),
            region_name=c.meta["source"].get("region_name", ""),
        )
        for c in candidates
    ]
    return AnalogsResponse(style=AnalogsStyle(**style), wines=wines)
