"""Dataclasses контракта (contracts/rag-interface.md v0.2), дословно.

Вынесены в отдельный модуль-лист (без зависимостей на остальной rag/),
чтобы избежать циклических импортов: rag.base собирает Retriever из
нескольких модулей, которым, в свою очередь, нужны Filters/Candidate.
rag.base реэкспортирует оба имени, так что `from rag.base import Filters`
(как записано в контракте) работает как и было задумано.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Filters:
    color: str | None = None
    sugar: str | None = None
    region: str | None = None
    grapes: list[str] = field(default_factory=list)
    stillness: str | None = None  # тихое | игристое


@dataclass
class Candidate:
    id: str  # wine slug | "article:<slug>#<n>" | "winery:<slug>"
    kind: str  # wine | chunk | winery
    score: float
    text: str  # текст для промпта
    url: str  # первоисточник для цитаты
    meta: dict
