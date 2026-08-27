"""Общие текстовые утилиты: токенизация для BM25, нормализация строк."""
from __future__ import annotations

import re
from functools import lru_cache

_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁ]+", re.UNICODE)


@lru_cache(maxsize=1)
def _stemmer():
    # NLTK Snowball — чистый Python, без скачивания моделей/словарей.
    # Зачем вообще стемминг: русский язык сильно флективен — «вино ИЗ
    # Дагестана»/«крымское вино» не пересекаются токенами с card-текстом
    # «... Дагестан ...»/«... Крым ...» без сведения к общей основе. Замер
    # на голд-сете подтвердил провал retrieval именно на region-специфичных
    # запросах (см. отчёт, раздел «pick») — стемминг ощутимо помогает.
    from nltk.stem.snowball import SnowballStemmer

    return SnowballStemmer("russian")


def tokenize(text: str | None, *, stem: bool = True) -> list[str]:
    """Lower-case токенайзер под кириллицу/латиницу для BM25, со стеммингом
    русских токенов (SnowballStemmer). `stem=False` — сырые токены (для
    отладки/тестов, где важна точность до буквы)."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    if not stem:
        return tokens
    stemmer = _stemmer()
    return [stemmer.stem(t) for t in tokens]


def normalize_ws(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
