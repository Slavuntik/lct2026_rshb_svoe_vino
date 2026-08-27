"""Детерминированное извлечение Filters из реплики /chat — ДО вызова
search(), без LLM. contracts/rag-interface.md, уточнения v0.2.4: "извлечение
Filters из реплики до вызова search() — ответственность вызывающего (/chat,
волна 3), не ретривера."

Источники словарей (никакой морфологии/ML — явные regex по стемам, ошибка
видна и чинится руками, в отличие от недетерминированной LLM-экстракции):
  - цвет/сахар — те же значения, что таксономия портала-источника vines
    (/Users/vyacheslavfokin/ClaudeWorkspace/vines/ref/taxonomy.yaml,
    read-only, поля colors/sugar_categories);
  - регион — slug+name из той же taxonomy.yaml (regions), read-only.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from ..rag.interface import Filters

TAXONOMY_PATH = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/ref/taxonomy.yaml")

# Порядок важен: более специфичные ПЕРЕД более общими — "полусухое"/
# "полусладкое"/"экстра брют" иначе ошибочно матчились бы как "сухое"/
# "сладкое"/"брют" (первое совпадение по списку побеждает).
_COLOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bкрасн"), "красное"),
    (re.compile(r"\bбел"), "белое"),
    (re.compile(r"\bрозов"), "розовое"),
    (re.compile(r"\bоранж"), "оранжевое"),
]

_SUGAR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bэкстра.?брют"), "экстра брют"),
    (re.compile(r"\bполусух"), "полусухое"),
    (re.compile(r"\bполуслад"), "полусладкое"),
    (re.compile(r"\bбрют"), "брют"),
    (re.compile(r"\bсух"), "сухое"),
    (re.compile(r"\bсладк"), "сладкое"),
]

# Грубое (не лингвистическое) отсечение типичных окончаний русских
# существительных/прилагательных — только чтобы "Кубани"/"Крыма"/"донское"
# матчились по той же основе, что и словарная форма. Длинные суффиксы
# проверяются раньше коротких, чтобы не отрезать лишнее по ошибке.
_RU_SUFFIXES = sorted({"ого", "его", "ими", "ыми", "ая", "ое", "ий", "ый", "ье", "ья",
                       "а", "я", "о", "е", "ы", "и", "ь"}, key=len, reverse=True)


def _stem(word: str) -> str:
    word = word.lower()
    for suf in _RU_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


@lru_cache(maxsize=1)
def _region_patterns() -> tuple[tuple[re.Pattern, str], ...]:
    """(regex, slug) из ref/taxonomy.yaml::regions — по каждому слову имени
    региона длиной >=3 после грубого отсечения окончания (для многословных
    имён вроде "Долина Дона" оба слова становятся триггерами: "долин" и
    "дон" — в разговоре чаще звучит только "Дон"/"донское")."""
    try:
        data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return ()
    patterns: list[tuple[re.Pattern, str]] = []
    seen_stems: set[str] = set()
    for region in data.get("regions", []):
        slug, name = region.get("slug"), region.get("name")
        if not slug or not name:
            continue
        for word in name.replace("—", " ").split():
            stem = _stem(word)
            if len(stem) >= 3 and stem not in seen_stems:
                seen_stems.add(stem)
                patterns.append((re.compile(rf"\b{re.escape(stem)}"), slug))
    return tuple(patterns)


def extract_filters(message: str) -> Filters:
    """Разбирает сырую реплику пользователя в Filters(color, sugar, region).
    Детерминированно, без сети и без LLM. Ничего не распозналось — пустой
    Filters() (все None, не режет кандидатов)."""
    text = message.lower()
    color = next((v for pat, v in _COLOR_PATTERNS if pat.search(text)), None)
    sugar = next((v for pat, v in _SUGAR_PATTERNS if pat.search(text)), None)
    region = next((slug for pat, slug in _region_patterns() if pat.search(text)), None)
    return Filters(color=color, sugar=sugar, region=region)
