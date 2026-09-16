from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_retriever_dep
from ..errors import ApiError
from ..rag.cards import build_wine_card
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
    # build_wine_card — общий с card в rich-режиме /scan/photo (v0.4.1,
    # app/rag/cards.py) — одна форма на оба места, не может снова разойтись.
    card = build_wine_card(retriever, wine_id)
    if card is None:
        raise ApiError(404, "not_found", "Вино не найдено")
    return WineResponse(**card)
