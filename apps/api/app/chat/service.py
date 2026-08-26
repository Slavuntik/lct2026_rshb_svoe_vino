"""Оркестрация чата: retrieval (мок-RAG) -> промпт -> LLM -> цитаты.

Пустая выдача ретривера => refusal без обращения к LLM (contracts/openapi.yaml
v0.2: "пустая выдача ретривера => refusal с честным текстом"). Дополнительно
(защита в глубину, за рамками буквы контракта, но в его духе): если финальный
ответ модели не содержит ни одного валидного маркера [n] несмотря на
непустой контекст — тоже refusal, а не выдача неподтверждённого текста.
Мок-драйвер (packages/llm) по построению всегда цитирует всё, что ему дали,
так что этот путь реалистично может сработать только на настоящих
провайдерах, если те проигнорируют системный промпт.

Упрощение MVP: ответ LLM буферизуется целиком ПЕРЕД тем, как отдать его
клиенту как последовательность SSE token-событий (а не проксируется потоково
чанк-в-чанк). Так проще гарантировать «токены, потом цитаты, потом done» и
реализовать защитный refusal-фоллбэк выше без сложного парсинга частичных
[n]-маркеров на границах чанков. Отдельные token-события в SSE всё равно
эмитятся (клиент получает тот же протокол), в жертву приносится только
задержка до первого байта — приемлемо для MVP/демо, честно задокументировано
здесь и в reports/b-report.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from llm.base import LLM, LLMUnavailable

from ..rag.interface import Candidate, Filters, Retriever
from .prompt import build_messages, extract_citation_numbers, format_taste_vector

EMPTY_RETRIEVAL_REASON = "В базе не нашлось вин или советов по вашему запросу — не готовы утверждать наугад."
UNGROUNDED_ANSWER_REASON = "Не удалось подкрепить ответ ссылками на источники — честнее промолчать, чем гадать."
LLM_UNAVAILABLE_REASON = "Сервис подсказок сомелье временно недоступен, попробуйте ещё раз через минуту."


@dataclass
class Citation:
    n: int
    kind: str  # wine | chunk
    ref_id: str  # wine_id | chunk_id
    url: str
    quote: str


@dataclass
class ChatOutcome:
    kind: str  # "answer" | "refusal"
    reason: str | None = None
    tokens: list[str] = field(default_factory=list)
    full_text: str = ""
    citations: list[Citation] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)


def run_chat(
    *,
    message: str,
    retriever: Retriever,
    llm: LLM,
    filters: Filters | None = None,
    filters_for_prompt: dict[str, str] | None = None,
    taste_vector: dict[str, float] | None = None,
    top_k: int = 8,
) -> ChatOutcome:
    candidates: list[Candidate] = retriever.search(
        message, filters=filters, collections=("wines", "knowledge"), top_k=top_k
    )
    if not candidates:
        return ChatOutcome(kind="refusal", reason=EMPTY_RETRIEVAL_REASON)

    taste_summary = format_taste_vector(taste_vector) if taste_vector else None
    messages = build_messages(
        message, candidates, filters=filters_for_prompt, taste_summary=taste_summary
    )

    try:
        tokens = list(llm.chat_stream(messages))
    except LLMUnavailable:
        return ChatOutcome(kind="refusal", reason=LLM_UNAVAILABLE_REASON)

    full_text = "".join(tokens)
    ns = extract_citation_numbers(full_text)
    valid_ns = [n for n in ns if 1 <= n <= len(candidates)]
    if not valid_ns:
        return ChatOutcome(kind="refusal", reason=UNGROUNDED_ANSWER_REASON)

    citations = []
    for n in valid_ns:
        c = candidates[n - 1]
        citations.append(
            Citation(n=n, kind=c.kind, ref_id=c.id, url=c.url, quote=c.text)
        )

    return ChatOutcome(
        kind="answer",
        tokens=tokens,
        full_text=full_text,
        citations=citations,
        candidate_ids=[c.id for c in candidates],
    )


def citation_to_event(citation: Citation) -> dict:
    event: dict = {"type": "citation", "n": citation.n, "url": citation.url, "quote": citation.quote}
    if citation.kind == "wine":
        event["wine_id"] = citation.ref_id
    else:
        event["chunk_id"] = citation.ref_id
    return event
