"""Общие текстовые утилиты: токенизация для BM25, нормализация строк."""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁ]+", re.UNICODE)


def tokenize(text: str | None) -> list[str]:
    """Простой lower-case токенайзер под кириллицу/латиницу для BM25.

    Не делает стемминг/лемматизацию — сознательно просто, чтобы не тащить
    дополнительные NLP-зависимости; при необходимости заменить в одном месте.
    """
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def normalize_ws(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
