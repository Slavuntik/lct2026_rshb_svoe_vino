"""Сборка промпта чата.

Гигиена по contracts/llm-adapter.md: "В messages НИКОГДА не попадают: email,
id пользователя, сырые события, геолокация. Вкусовой паспорт передаётся
строкой вида `sweetness=0.3 acidity=0.7 ...` без идентичности."

Инвариант обеспечен СТРУКТУРНО: build_messages() физически не принимает ни
email, ни user_id, ни объект пользователя — только сообщение, выдержки,
словарь фильтров и (опционально) уже обезличенную строку вкусового профиля.
Тест apps/api/tests/test_chat.py::test_email_never_reaches_llm_prompt
прогоняет это через реальный пайплайн чата на пользователе с email и
проверяет, что email не встречается ни в одном итоговом Msg.
"""
from __future__ import annotations

import re

from llm.base import Msg

from ..rag.interface import Candidate

SYSTEM_PROMPT = (
    "Ты — сомелье-ассистент «Свой Сомелье». Отвечай ТОЛЬКО по выдержкам, "
    "приведённым ниже в контексте. Каждое фактическое утверждение обязано "
    "заканчиваться ссылкой вида [n], где n — номер выдержки в контексте. "
    "Если выдержек недостаточно, чтобы честно ответить на вопрос — прямо "
    "откажись, не выдумывай факты и не советуй, где купить алкоголь."
)

_AXIS_ORDER = (
    "sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles",
)

_CITATION_RE = re.compile(r"\[(\d+)\]")


def format_taste_vector(vector: dict[str, float]) -> str:
    """`{"sweetness": 0.3, ...}` -> `"sweetness=0.3 acidity=0.7 ..."` — без id/имени."""
    parts = [f"{axis}={vector[axis]:.2f}" for axis in _AXIS_ORDER if axis in vector]
    return " ".join(parts)


def build_excerpts_block(candidates: list[Candidate]) -> str:
    return "\n".join(f"[{i}] {c.text}" for i, c in enumerate(candidates, start=1))


def build_messages(
    message: str,
    candidates: list[Candidate],
    *,
    filters: dict[str, str] | None = None,
    taste_summary: str | None = None,
) -> list[Msg]:
    parts = ["Контекст (используй только эти выдержки, не придумывай ничего сверх них):",
             build_excerpts_block(candidates)]
    active_filters = {k: v for k, v in (filters or {}).items() if v}
    if active_filters:
        parts.append("Фильтры пользователя: " + ", ".join(f"{k}={v}" for k, v in active_filters.items()))
    if taste_summary:
        parts.append(f"Вкусовой профиль пользователя (обезличенный вектор 0..1): {taste_summary}")
    parts.append(f"Вопрос пользователя: {message}")
    user_content = "\n\n".join(parts)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def extract_citation_numbers(text: str) -> list[int]:
    """Номера [n] в порядке первого появления, без повторов."""
    seen: list[int] = []
    for match in _CITATION_RE.finditer(text):
        n = int(match.group(1))
        if n not in seen:
            seen.append(n)
    return seen
