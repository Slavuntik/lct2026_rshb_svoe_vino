"""Mock-драйвер: детерминированные ответы, без сети и без ключей.

Конвенция (приватный под-контракт с apps/api/app/chat/prompt.py, обе стороны
живут в зоне агента B): промпт-сборщик кладёт выдержки для цитирования в виде
строк вида ``[1] текст выдержки`` (по одной на строку, без переносов внутри
самой выдержки) в любое из сообщений. Mock-драйвер построчно ищет такие
маркеры во ВСЕХ сообщениях (system+user) и собирает ответ, детерминированно
цитируя каждую найденную выдержку — так API и клиент разрабатываются без
единого реального ключа (см. contracts/llm-adapter.md).

Если маркеров нет вообще — значит вызывающая сторона не подставила контекст;
мок отвечает без цитат (вызывающий код должен трактовать безцитатный ответ
как повод для refusal, а не выдавать его пользователю как факт).
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from ..base import Msg

_EXCERPT_RE = re.compile(r"^\[(\d+)\]\s+(.+)$", re.MULTILINE)

_MAX_SNIPPET_CHARS = 160


def _first_clause(text: str) -> str:
    text = " ".join(text.split())  # схлопнуть переносы/повторные пробелы
    if len(text) <= _MAX_SNIPPET_CHARS:
        return text
    return text[:_MAX_SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _extract_excerpts(messages: list[Msg]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for m in messages:
        for match in _EXCERPT_RE.finditer(m["content"]):
            found.append((match.group(1), match.group(2)))
    return found


def _compose_answer(messages: list[Msg]) -> str:
    excerpts = _extract_excerpts(messages)
    if not excerpts:
        return (
            "В предоставленных материалах не нашлось выдержек по вопросу — "
            "не готов утверждать что-либо без источника."
        )
    clauses = [f"{_first_clause(text)} [{n}]" for n, text in excerpts]
    return "По данным источников: " + " ".join(clauses)


class MockLLM:
    """Детерминированный драйвер для тестов и разработки без ключей."""

    provider = "mock"
    model = "mock-1"

    def chat(
        self,
        messages: list[Msg],
        *,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        answer = _compose_answer(messages)
        answer = answer[: max_tokens * 4]
        if json_mode:
            return json.dumps({"answer": answer}, ensure_ascii=False)
        return answer

    def chat_stream(
        self,
        messages: list[Msg],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        answer = self.chat(messages, max_tokens=max_tokens, temperature=temperature)
        words = answer.split(" ")
        for i, w in enumerate(words):
            yield w if i == len(words) - 1 else w + " "
