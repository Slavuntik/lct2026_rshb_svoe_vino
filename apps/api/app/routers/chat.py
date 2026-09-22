"""POST /chat (SSE) и POST /chat/feedback.

/chat требует принципала (гость или пользователь) — без этого 18+ гейт
обходился бы прямым вызовом API мимо /auth/guest|register (см.
app/security.py докстринг). Ретривер и LLM подставляются из app.state
(app/deps.py) — в dev/test это MockRetriever + MockLLM, переключение на
реальные — только через env (RAG_PROVIDER/LLM_PROVIDER), без ветвления в
этом файле.

v0.3: настоящий стриминг — event_stream() ниже потребляет
chat.service.stream_chat_events() построчно и пробрасывает каждый token
клиенту немедленно (никакой list()/буферизации между LLM и SSE, см.
app/chat/service.py докстринг). Filters на /chat теперь — объединение
явных полей запроса (приоритет) и того, что извлекается из текста реплики
детерминированно (app/chat/filters.py, contracts/rag-interface.md v0.2.4).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from llm.base import LLM
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..chat.filters import extract_filters
from ..chat.service import citation_to_event, stream_chat_events
from ..config import Settings, get_settings_dep
from ..db import get_db
from ..deps import get_llm_dep, get_retriever_dep
from ..errors import ApiError
from ..models import ChatMessage, Feedback, TasteProfile
from ..rag.interface import Filters, Retriever
from ..schemas import ChatFeedbackRequest, ChatRequest
from ..security import Principal, get_current_principal

router = APIRouter(tags=["chat"])


def _merge_filters(explicit: Filters, extracted: Filters) -> Filters:
    """Явные поля запроса выигрывают (осознанный UI-выбор пользователя);
    извлечённые из текста реплики дозаполняют то, что клиент не указал."""
    return Filters(
        color=explicit.color or extracted.color,
        sugar=explicit.sugar or extracted.sugar,
        region=explicit.region or extracted.region,
    )


@router.post("/chat")
def chat(
    body: ChatRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    llm: LLM = Depends(get_llm_dep),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
) -> EventSourceResponse:
    filters_model = body.filters
    explicit_filters = Filters(
        color=filters_model.color if filters_model else None,
        sugar=filters_model.sugar if filters_model else None,
        region=filters_model.region if filters_model else None,
    )
    filters = _merge_filters(explicit_filters, extract_filters(body.message))
    filters_for_prompt = {
        k: v for k, v in
        {"color": filters.color, "sugar": filters.sugar, "region": filters.region}.items()
        if v
    } or None

    # Вкусовой профиль — только числовой вектор без идентичности (см.
    # app/chat/prompt.py::format_taste_vector и contracts/llm-adapter.md).
    taste_vector: dict[str, float] | None = None
    if principal.kind == "user":
        profile = db.get(TasteProfile, principal.id)
        if profile is not None:
            taste_vector = profile.vector

    def event_stream():
        for event in stream_chat_events(
            message=body.message, retriever=retriever, llm=llm,
            filters=filters, filters_for_prompt=filters_for_prompt, taste_vector=taste_vector,
            # reports/backend-chat-retrieval.md (22.09, п.3): бюджет длины ответа —
            # без лимита первый прогон стенда ушёл на 497 токенов / ~84 с до конца.
            max_tokens=settings.chat_max_tokens,
        ):
            if event["type"] == "token":
                yield {"data": json.dumps(event, ensure_ascii=False)}
                continue

            if event["type"] == "refusal":
                yield {"data": json.dumps(event, ensure_ascii=False)}
                return

            # event["type"] == "_ready": не SSE-тип сам по себе — сигнал
            # "стрим токенов закончен, пора персистить и закрывать".
            db.add(ChatMessage(
                user_id=principal.id, role="user", content=body.message,
                citations=[], trace=None,
            ))
            # Persisted citations используют ту же форму, что и SSE citation-событие
            # (citation_to_event) — единый источник истины для формы {n, wine_id|
            # chunk_id, url, quote}, как в комментарии contracts/schema.sql.
            assistant_msg = ChatMessage(
                user_id=principal.id, role="assistant", content=event["full_text"],
                citations=[
                    {k: v for k, v in citation_to_event(c).items() if k != "type"}
                    for c in event["citations"]
                ],
                trace={
                    "filters": filters_for_prompt or {},
                    "candidate_ids": event["candidate_ids"],
                    "index_version": getattr(retriever, "index_version", None) or settings.rag_index_version,
                },
            )
            db.add(assistant_msg)
            db.commit()
            db.refresh(assistant_msg)

            for citation in event["citations"]:
                yield {"data": json.dumps(citation_to_event(citation), ensure_ascii=False)}

            yield {"data": json.dumps({"type": "done", "answer_id": str(assistant_msg.id)}, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


@router.post("/chat/feedback", status_code=204)
def chat_feedback(
    body: ChatFeedbackRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    try:
        message_id = int(body.answer_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", "Ответ с таким answer_id не найден") from exc

    message = db.get(ChatMessage, message_id)
    if message is None or message.role != "assistant":
        raise ApiError(404, "not_found", "Ответ с таким answer_id не найден")

    db.add(Feedback(user_id=principal.id, message_id=message_id, verdict=body.verdict))
    db.commit()
    return None
