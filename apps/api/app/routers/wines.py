from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_retriever_dep
from ..errors import ApiError
from ..rag.interface import Retriever
from ..schemas import WineResponse
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/wines", tags=["wines"])


@router.get("/{wine_id}", response_model=WineResponse)
def get_wine(
    wine_id: str,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
) -> WineResponse:
    # Retriever (contracts/rag-interface.md) не объявляет метод точечного
    # получения карточки по id — только search/resolve_label/similar/
    # analog_for_style/resolve_style. get_by_id — расширение MockRetriever
    # сверх контракта (см. app/rag/interface.py докстринг и предложения к
    # контракту в reports/b-report.md). Деградация без него — честный 404.
    getter = getattr(retriever, "get_by_id", None)
    wine = getter(wine_id) if getter else None
    if wine is None:
        raise ApiError(404, "not_found", "Вино не найдено")

    source = {k: v for k, v in wine.items() if k not in ("slug", "derived", "source_url")}
    similar = [c.id for c in retriever.similar(wine_id, top_k=6)]
    return WineResponse(
        wine_id=wine_id, source=source, derived=wine["derived"],
        source_url=wine["source_url"], similar=similar,
    )
