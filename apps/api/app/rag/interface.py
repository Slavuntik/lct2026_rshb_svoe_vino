"""Зеркало contracts/rag-interface.md v0.2 (ЗАМОРОЖЁН, правки — через
оркестратора). Пакет packages/rag — зона агента A; на момент написания этого
кода он ещё/пока не содержит рабочего packages/rag/rag/base.py, а мы не имеем
права ждать и не имеем права туда писать (agents/B-api.md: "не жди его").

Поэтому Filters/Candidate/Retriever продублированы здесь дословно как
структурный (duck-typed) контракт: MockRetriever в mock.py реализует именно
этот Protocol, а app/rag/factory.py умеет переключиться на настоящий
packages/rag.rag.base.Retriever через RAG_PROVIDER=real, если/когда пакет A
появится и будет установлен в это окружение (см. factory.py).

Предложение к контракту (см. reports/b-report.md): в Retriever нет метода
получения карточки вина по id (для /wines/{wine_id}) — есть только search /
resolve_label / similar / analog_for_style / resolve_style. MockRetriever
поэтому добавляет get_by_id(...) сверх контракта; фабрика деградирует
аккуратно, если у реального ретривера такого метода нет (см. factory.py).
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
