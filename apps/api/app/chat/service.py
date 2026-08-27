"""Оркестрация чата: retrieval (мок-RAG) -> промпт -> LLM -> цитаты.

Пустая выдача ретривера => refusal без обращения к LLM (contracts/openapi.yaml:
"пустая выдача ретривера => refusal с честным текстом").

v0.3 (ревью 02, блокер 2): стриминг НАСТОЯЩИЙ. stream_chat_events() —
ГЕНЕРАТОР: каждый чанк от llm.chat_stream() пробрасывается наружу как
token-событие СРАЗУ, по мере того как драйвер его производит — никакого
`list(llm.chat_stream(...))` или иной eager-материализации перед тем, как
начать отвечать клиенту. Это прямое требование контракта: "буферизация
полного ответа запрещена: молчание 5-10 с на сцене демо — это провал сцены".

Побочный эффект настоящего стриминга: старый защитный приём "если в ответе
нет ни одного валидного [n] — тихо подменить его на refusal" для уже
отправленных токенов невозможен (нельзя отозвать то, что клиент уже
показал). Поэтому:
  - пустая выдача ретривера (до обращения к LLM) — по-прежнему refusal,
    железно, токены ещё не отправлялись;
  - LLMUnavailable ДО первого чанка — тоже refusal;
  - LLMUnavailable ПОСЛЕ первого чанка — генератор закрывает поток как
    обычный (пусть и оборванный) ответ, см. ниже про цитаты.

v0.3.2 (по флагу B в reports/b-report.md, контракт openapi.yaml/описание
/chat): если генерация завершилась БЕЗ единого валидного [n] в тексте,
сервер перед done обязан отправить citation-события по ВСЕМ выдержкам,
использованным в промпте (нумерация n продолжается 1..len(candidates),
соответствия маркерам в тексте нет — и это законно по контракту). Гарантия
"каждый ответ несёт источники" восстановлена по построению: раз ответ
показан, контекст промпта и есть его источник, даже если модель не
расставила ссылки сама. Твёрдый барьер против галлюцинаций на пустой
выдаче ретривера (до обращения к LLM) и системный промпт (app/chat/prompt.py)
— это про то, ЧТО подаётся модели; данная правка — про то, что клиент
ВСЕГДА видит, откуда взялся ответ, даже если сама модель цитаты не расставила.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from llm.base import LLM, LLMUnavailable

from ..rag.interface import Candidate, Filters, Retriever
from .prompt import build_messages, extract_citation_numbers, format_taste_vector

EMPTY_RETRIEVAL_REASON = "В базе не нашлось вин или советов по вашему запросу — не готовы утверждать наугад."
LLM_UNAVAILABLE_REASON = "Сервис подсказок сомелье временно недоступен, попробуйте ещё раз через минуту."


@dataclass
class Citation:
    n: int
    kind: str  # wine | chunk
    ref_id: str  # wine_id | chunk_id
    url: str
    quote: str


def citation_to_event(citation: Citation) -> dict:
    event: dict = {"type": "citation", "n": citation.n, "url": citation.url, "quote": citation.quote}
    if citation.kind == "wine":
        event["wine_id"] = citation.ref_id
    else:
        event["chunk_id"] = citation.ref_id
    return event


def stream_chat_events(
    *,
    message: str,
    retriever: Retriever,
    llm: LLM,
    filters: Filters | None = None,
    filters_for_prompt: dict[str, str] | None = None,
    taste_vector: dict[str, float] | None = None,
    top_k: int = 8,
) -> Iterator[dict]:
    """Генератор логических событий чата — потребляется routers/chat.py.

    Формы yield:
      {"type": "refusal", "reason": str}                                   — терминально
      {"type": "token", "text": str}                                       — по мере генерации
      {"type": "_ready", "full_text": str, "citations": [Citation, ...],
       "candidate_ids": [str, ...]}                                        — терминально, не SSE-тип
                                                                              как есть: роутер сначала
                                                                              персистит, потом сам
                                                                              эмитит citation+done.
    """
    candidates: list[Candidate] = retriever.search(
        message, filters=filters, collections=("wines", "knowledge"), top_k=top_k
    )
    if not candidates:
        yield {"type": "refusal", "reason": EMPTY_RETRIEVAL_REASON}
        return

    taste_summary = format_taste_vector(taste_vector) if taste_vector else None
    messages = build_messages(
        message, candidates, filters=filters_for_prompt, taste_summary=taste_summary
    )

    chunks: list[str] = []
    try:
        for chunk in llm.chat_stream(messages):
            chunks.append(chunk)
            yield {"type": "token", "text": chunk}
    except LLMUnavailable:
        if not chunks:
            yield {"type": "refusal", "reason": LLM_UNAVAILABLE_REASON}
            return
        # Токены уже ушли клиенту — откатить нельзя (см. модульный докстринг).
        # Закрываем как обычный (пусть и оборванный) ответ ниже.

    full_text = "".join(chunks)
    if not full_text:
        # Ни одного чанка не показано — рефузить всё ещё честно и возможно.
        yield {"type": "refusal", "reason": LLM_UNAVAILABLE_REASON}
        return

    ns = extract_citation_numbers(full_text)
    valid_ns = [n for n in ns if 1 <= n <= len(candidates)]
    if not valid_ns:
        # v0.3.2: модель не расставила [n] — цитируем ВСЕ выдержки, реально
        # попавшие в промпт (непривязанные к тексту, но законные по
        # контракту): "каждый ответ несёт источники" гарантируется по
        # построению, а не надеждой на дисциплину модели.
        valid_ns = list(range(1, len(candidates) + 1))
    citations = [
        Citation(n=n, kind=candidates[n - 1].kind, ref_id=candidates[n - 1].id,
                 url=candidates[n - 1].url, quote=candidates[n - 1].text)
        for n in valid_ns
    ]

    yield {
        "type": "_ready",
        "full_text": full_text,
        "citations": citations,
        "candidate_ids": [c.id for c in candidates],
    }
