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


def filters_active(filters: "Filters | None") -> bool:
    """Хотя бы одно поле Filters распознано (reports/backend-chat-retrieval.md,
    22.09) — используется rag.intent.classify() как сигнал "вопрос про атрибут
    вина" и rag.base.Retriever.search() как условие "есть что ослаблять" перед
    честным refusal. Поля извлекаются детерминированно из таксономии вина
    (apps/api/app/chat/filters.py) — непустое поле само по себе не ловит
    посторонние вопросы ("Посоветуй фильм ужасов" не содержит ни цвета, ни
    сахара, ни региона).

    Нарочно СВОБОДНАЯ ФУНКЦИЯ через getattr, не метод класса: Filters сюда
    приходит от вызывающего (apps/api) как структурно совместимый (duck-typed)
    дубликат этого дата-класса — apps/api/app/rag/interface.py::Filters,
    ОТДЕЛЬНЫЙ класс с теми же полями (см. докстринг того модуля, "продублированы
    здесь дословно"), без гарантии тех же МЕТОДОВ. Остальной rag/ (rag.filtering,
    rag.store.build_filter) уже трактует Filters только через атрибуты по той
    же причине — метод класса здесь ломал бы duck-typing первым же реальным
    вызовом из apps/api (AttributeError, поймано локальной репродукцией на
    копии стендового индекса, см. отчёт)."""
    if filters is None:
        return False
    return bool(
        getattr(filters, "color", None)
        or getattr(filters, "sugar", None)
        or getattr(filters, "region", None)
        or getattr(filters, "grapes", None)
        or getattr(filters, "stillness", None)
    )


@dataclass
class Candidate:
    id: str  # wine slug | "article:<slug>#<n>" | "winery:<slug>"
    kind: str  # wine | chunk | winery
    score: float
    text: str  # текст для промпта
    url: str  # первоисточник для цитаты
    meta: dict
