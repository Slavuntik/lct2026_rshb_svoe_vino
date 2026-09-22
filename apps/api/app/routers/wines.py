from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings_dep
from ..deps import get_retriever_dep
from ..errors import ApiError
from ..food_pairing import compute_pairings
from ..rag.cards import build_wine_card
from ..rag.interface import Retriever
from ..schemas import WinePairingsResponse, WineResponse
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


@router.get("/{wine_id}/pairings", response_model=WinePairingsResponse)
def get_wine_pairings(
    wine_id: str,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
) -> WinePairingsResponse:
    """Гастропары «к чему подать это вино» — contracts/post-scan.md v1.0 §1.
    Резолюция wine_id — ТА ЖЕ build_wine_card, что у GET /wines/{id} выше (не
    вторая копия наш-каталог/каталог-кейса); 404 not_found на тех же условиях.
    Три уровня данных — app/food_pairing.py::compute_pairings, никакого LLM.
    """
    card = build_wine_card(retriever, wine_id)
    if card is None:
        raise ApiError(404, "not_found", "Вино не найдено")
    result = compute_pairings(card, settings)
    return WinePairingsResponse(wine_id=wine_id, **result)
