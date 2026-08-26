"""POST /analogs — детерминированный путь «аналог импортного» (v0.2):
resolve_style -> analog_for_style, без обращения к LLM.
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
        # list_reference_styles — как и get_by_id в wines.py, расширение
        # MockRetriever сверх contracts/rag-interface.md (см. предложения к
        # контракту в reports/b-report.md); без него просто не подсказываем топ-5.
        lister = getattr(retriever, "list_reference_styles", None)
        top_styles = lister(limit=5) if lister else []
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
        AnalogsWineItem(
            wine_id=c.id, name=c.meta.get("name", c.id),
            winery_name=c.meta.get("winery_name", ""), region_name=c.meta.get("region_name", ""),
        )
        for c in candidates
    ]
    return AnalogsResponse(style=AnalogsStyle(**style), wines=wines)
