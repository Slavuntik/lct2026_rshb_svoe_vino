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
    # get_by_id — часть contracts/rag-interface.md с v0.2.1 (было предложением
    # агента B); возвращает Candidate с meta={"source":..., "derived":...}
    # для вина, а не сырой dict — см. app/rag/mock.py::_wine_to_full_candidate.
    candidate = retriever.get_by_id(wine_id)
    if candidate is None or candidate.kind != "wine":
        raise ApiError(404, "not_found", "Вино не найдено")

    similar = [c.id for c in retriever.similar(wine_id, top_k=6)]
    return WineResponse(
        wine_id=wine_id, source=candidate.meta["source"], derived=candidate.meta["derived"],
        source_url=candidate.url, similar=similar,
    )
