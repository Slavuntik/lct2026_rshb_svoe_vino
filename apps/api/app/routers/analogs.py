"""POST /analogs — детерминированный путь «аналог импортного» (v0.2):
resolve_style -> analog_for_style, без обращения к LLM. list_reference_styles
— часть contracts/rag-interface.md с v0.2.1 (было предложением агента B).

resolve_style -> analog_for_style (через wines_for_style) — общий вызов с
фолбэком /v1/scan/resolve на пустых matches (agents/B7-foreign-analogs.md);
сама функция вынесена в ../analog_lookup.py, чтобы не дублировать маппинг
Candidate -> AnalogsWineItem в двух роутерах.

Находка qa-manual (27.09, reports/qa-manual-final.md #4): карточка скана
автохтонного российского сорта (Рубин Голодриги, Красностоп Золотовский,
Цимлянский чёрный) зовёт этот эндпоинт "вслепую" по сорту из уже показанной
карточки (apps/web ScanScreen.tsx::resolveTasteAnalogs, тот же путь — и у
ChatScreen.tsx::handleAnalog для типизированного поиска) и рендерит
error.message дословно. resolve_style закономерно не находит иностранный
стиль для автохтона — это ЧЕСТНЫЙ пустой результат ("такого стиля у
зарубежных вин просто нет"), не сбой сервиса. Старый текст одновременно (а)
звучал как ошибка распознавания и (б) перечислял ИНОСТРАННЫЕ эталонные стили
на карточке РОССИЙСКОГО вина. Код/статус оставлены 404 not_found как в
contracts/openapi.yaml (см. reports/backend-analogs-empty.md, "Предложения к
контрактам" — почему код не сменён на 200: ScanScreen.tsx и ChatScreen.tsx на
успехе читают response.style.slug без null-проверки, TasteAnalogsState.
"ready" требует style непустым — смена кода без правки фронта увела бы в
необработанный TypeError вместо честного текста; фронт сейчас в работе
другого агента, трогать нельзя). Меняется только текст сообщения.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..analog_lookup import wines_for_style
from ..deps import get_retriever_dep
from ..errors import ApiError
from ..rag.interface import Filters, Retriever
from ..schemas import AnalogsRequest, AnalogsResponse, AnalogsStyle
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/analogs", tags=["analogs"])
logger = logging.getLogger(__name__)

# Честный пустой результат ("похожих по стилю иностранных вин не нашли"), а
# не техническая ошибка — и БЕЗ перечисления внутренних (иностранных)
# эталонных стилей каталога: на карточке автохтонного российского сорта
# список вроде "Монтепульчано д'Абруццо, Сансер, Кот дю Рон" не несёт
# пользы и выглядит как утечка внутренностей (reports/qa-manual-final.md #4).
_NOT_FOUND_MESSAGE = "Честно: похожих по стилю иностранных вин не нашли."


@router.post("", response_model=AnalogsResponse)
def analogs(
    body: AnalogsRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
) -> AnalogsResponse:
    style = retriever.resolve_style(body.query)
    if style is None:
        # Топ популярных стилей больше НЕ идёт пользователю (см. докстринг
        # модуля) — только в лог, для отладки словаря стилей.
        top_styles = retriever.list_reference_styles(top_n=5)
        logger.info(
            "analogs: стиль не распознан (query=%r); ближайшие популярные стили справочника=%r",
            body.query, [s["slug"] for s in top_styles],
        )
        raise ApiError(404, "not_found", _NOT_FOUND_MESSAGE)

    filters = Filters(
        region=body.filters.region if body.filters else None,
        sugar=body.filters.sugar if body.filters else None,
    )
    wines = wines_for_style(retriever, style["slug"], filters=filters, top_k=12)
    return AnalogsResponse(style=AnalogsStyle(**style), wines=wines)
