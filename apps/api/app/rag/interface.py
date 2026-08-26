"""Зеркало contracts/rag-interface.md v0.2.1 (ЗАМОРОЖЁН, правки — через
оркестратора). Пакет packages/rag — зона агента A; на момент написания этого
кода он не содержит ещё get_by_id/list_reference_styles/get_retriever (только
что появились в контракте v0.2.1 по предложению агента B — см.
reports/b-report.md), а мы не имеем права ждать (agents/B-api.md: "не жди
его").

Поэтому Filters/Candidate/Retriever продублированы здесь дословно как
структурный (duck-typed) контракт: MockRetriever в mock.py реализует именно
этот Protocol, а app/rag/factory.py умеет переключиться на настоящий
packages/rag через RAG_PROVIDER=real, если/когда пакет A появится и будет
установлен в это окружение (см. factory.py) — предпочтительно через
rag.get_retriever(), теперь тоже часть контракта.

v0.2.2 (GET /taste/candidates, пробел нашёл агент C): MockRetriever.
candidates_for_taste(exclude_ids, limit) — NB, это НЕ в Protocol ниже и НЕ в
contracts/rag-interface.md — та правка v0.2.2 коснулась только openapi.yaml.
routers/taste.py обращается к нему через getattr(..., None) с деградацией в
пустую колоду, если у ретривера такого метода нет (тот же паттерн, что был
у get_by_id/list_reference_styles до их включения в контракт в v0.2.1) —
кандидат на следующую версию contracts/rag-interface.md, см. reports/b-report.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


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


class Retriever(Protocol):
    def search(
        self,
        query: str,
        *,
        filters: Filters | None = None,
        collections: tuple[str, ...] = ("wines", "knowledge"),
        top_k: int = 8,
    ) -> list[Candidate]: ...

    def resolve_label(self, text: str, hints: dict | None = None) -> list[Candidate]:
        """Для /scan/resolve: fuzzy по name+winery_name (rapidfuzz), НЕ векторный поиск."""

    def similar(self, wine_id: str, top_k: int = 6) -> list[Candidate]: ...

    def analog_for_style(
        self, style_slug: str, *, filters: Filters | None = None, top_k: int = 12
    ) -> list[Candidate]:
        """«Аналог импортного»: reference_style -> вина с этим стилем в derived."""

    def resolve_style(self, query: str) -> dict | None:
        """«люблю Просекко» -> {"slug": ..., "name": ..., "country": ...}.
        Fuzzy по name/slug/синонимам; None, если не распознан. (v0.2)"""

    def get_by_id(self, id: str) -> Candidate | None:
        """Карточка по id (wine-slug | article:<slug>#<n> | winery:<slug>) —
        для /wines/{id}. v0.2.1 (предложение агента B)."""

    def list_reference_styles(self, top_n: int = 5) -> list[dict]:
        """Популярные стили ({"slug","name","country"}) — подсказка в 404
        /analogs. v0.2.1 (предложение агента B)."""


def get_retriever() -> Retriever:  # pragma: no cover - см. app/rag/factory.py
    """Сигнатура из contracts/rag-interface.md v0.2.1: фабрика БЕЗ аргументов,
    симметрично packages/llm.get_llm(). Здесь не реализована — это подпись
    ожидаемой точки входа настоящего packages/rag, вызываемой из
    app/rag/factory.py при RAG_PROVIDER=real. apps/api вместо неё использует
    свою app.rag.factory.get_retriever(settings), которая либо строит
    MockRetriever, либо делегирует в эту функцию из настоящего пакета rag.
    """
    raise NotImplementedError("реализация — в пакете packages/rag, не здесь")
