"""Rank catalog wines for the shelf extension using the main sommelier's data.
CV remains independent; this endpoint never guesses an identity from an image.
"""

from __future__ import annotations

import re
from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ConfigDict

from .. import dish_pairing
from ..chat.filters import extract_filters
from ..config import Settings, get_settings_dep
from ..deps import get_retriever_dep
from ..dish_recognition import resolve_category
from ..food_pairing import (
    _load_rules_data,
    _rules_path,
    portal_tags,
    score_wine_for_dish,
)
from ..rag.interface import Retriever
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/sommelier", tags=["sommelier"])


class ShelfSelectionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    wish: str = Field(min_length=2, max_length=1000)
    # None previews the catalog selection; [] explicitly means nothing was found.
    wine_ids: list[Annotated[str, Field(min_length=1, max_length=300)]] | None = Field(
        default=None, max_length=640
    )
    dish: str | None = Field(default=None, max_length=100)


class RankedWine(BaseModel):
    wine_id: str
    name: str
    rank: int
    reason: str
    basis: str


class ShelfSelectionResponse(BaseModel):
    wines: list[RankedWine]
    understood: list[str]
    warnings: list[str]
    message: str


@router.post("/shelf-selection", response_model=ShelfSelectionResponse)
def shelf_selection(
    body: ShelfSelectionRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
):
    wish = body.wish.strip()
    # Reuse /chat extraction, removing simple exclusions before positive parsing.
    negatives = re.findall(
        r"\b(?:не(?:\s+(?:хочу|люблю|надо))?|без)\s+(красн\w*|бел\w*|розов\w*|сладк\w*|полусладк\w*|сух\w*)",
        wish.casefold(),
    )
    positive = re.sub(
        r"\b(?:не(?:\s+(?:хочу|люблю|надо))?|без)\s+(?:красн\w*|бел\w*|розов\w*|сладк\w*|полусладк\w*|сух\w*)",
        "",
        wish,
        flags=re.I,
    )
    filters = extract_filters(positive)
    excluded = [extract_filters(word) for word in negatives]
    tags = portal_tags(settings)
    dish = resolve_category(body.dish, tags) if body.dish else None
    if not dish:
        # Infer food only after an explicit pairing preposition. "Сладкое"
        # alone describes wine, not an implicit request for a dessert pairing.
        food = re.search(r"\b(?:к|под)\s+([^,.;]+)", positive.casefold())
        if food:
            phrase = re.sub(r"\bрыб[еуы]\b", "рыба", food[1])
            phrase = re.sub(r"\bмяс[уы]\b", "мясо", phrase)
            phrase = re.sub(r"\bстейк[уае]\b", "стейк", phrase)
            dish = resolve_category(phrase, tags)
    understood = [v for v in (filters.color, filters.sugar, filters.region, dish) if v]
    understood += [f"Без: {word}" for word in negatives]
    warnings = []
    if re.search(r"\d|цен|руб|бюдж|недорог", wish, re.I):
        warnings.append(
            "Цены на полках не проверяются: бюджет нужно сверить по ценнику."
        )
    if body.dish and not resolve_category(body.dish, tags):
        warnings.append("Категория блюда не распознана.")
    if not understood:
        warnings.append(
            "Подбор по текстовой близости. Уточните цвет, сладость или блюдо для более точного выбора."
        )
    allowed = set(body.wine_ids) if body.wine_ids is not None else None
    if allowed == set():
        return ShelfSelectionResponse(
            wines=[],
            understood=understood,
            warnings=warnings,
            message="На снимках пока нет уверенно распознанных вин из базы.",
        )
    hits = retriever.search(positive, filters=filters, collections=("wines",), top_k=50)
    relevance = {
        hit.id: max(0.0, float(hit.score)) for hit in hits if hit.kind == "wine"
    }
    rules = _load_rules_data(_rules_path(settings)) if dish else {}
    dish_vector = (rules.get("portal_tag_defaults") or {}).get(dish, {})
    ranked = []
    for wine_id, source, vector in dish_pairing.get_catalog_cards(retriever, settings):
        if allowed is not None and wine_id not in allowed:
            continue
        color, sugar, region = (
            source.get("color"),
            source.get("sugar_category"),
            source.get("region"),
        )
        if any(
            wanted and str(actual or "").casefold() != wanted.casefold()
            for wanted, actual in (
                (filters.color, color),
                (filters.sugar, sugar),
                (filters.region, region),
            )
        ):
            continue
        if any(
            (ex.color and ex.color == str(color or "").casefold())
            or (ex.sugar and ex.sugar == str(sugar or "").casefold())
            for ex in excluded
        ):
            continue
        reasons = [v for v in (filters.color, filters.sugar, filters.region) if v]
        tier, fit, basis = 0, 0.0, "catalog-filters"
        if dish:
            scored = score_wine_for_dish(dish_vector, vector, rules) if vector else None
            if dish in (source.get("food_pairings") or []):
                tier, fit, basis = 2, scored[0] if scored else 0, "catalog-pairing"
                reasons.append(f"В каталоге рекомендовано к «{dish}»")
            elif scored:
                tier, fit, basis = 1, scored[0], "pairing-rules"
                reasons.append(scored[1] or f"Подбор по правилам к «{dish}»")
            else:
                continue
        elif not reasons and not negatives:
            if wine_id not in relevance:
                continue
            basis = "text-search"
            reasons.append("Близко к запросу по поиску сомелье; проверьте описание")
        if not reasons:
            reasons.append("Соответствует указанным исключениям")
        ranked.append(
            (tier, fit, relevance.get(wine_id, 0), wine_id, source, reasons, basis)
        )
    ranked.sort(key=lambda r: (-r[0], -r[1], -r[2], r[3]))
    wines = [
        RankedWine(
            wine_id=row[3],
            name=row[4].get("name") or row[3],
            rank=i + 1,
            reason=" · ".join(row[5]),
            basis=row[6],
        )
        for i, row in enumerate(ranked[:50])
    ]
    return ShelfSelectionResponse(
        wines=wines,
        understood=understood,
        warnings=warnings,
        message=(
            "Подходящие варианты — сначала наиболее релевантные."
            if wines
            else "Подходящих подтверждённых вариантов нет. Добавьте фото другой полки или уточните пожелание."
        ),
    )
