"""Зеркало contracts/rag-interface.md v0.3 (ЗАМОРОЖЁН, правки — через
оркестратора). Пакет packages/rag — зона агента A; реализован независимо по
тому же контракту (см. packages/rag/rag/base.py, packages/rag/rag/meta.py).

Filters/Candidate/Retriever продублированы здесь дословно как структурный
(duck-typed) контракт: MockRetriever в mock.py реализует именно этот
Protocol, а app/rag/factory.py умеет переключиться на настоящий packages/rag
через RAG_PROVIDER=real (rag.get_retriever(), тоже часть контракта — v0.2.1).

v0.3 (ревью 02, блокер 1) зафиксировала форму Candidate.meta — она ЗАКОН,
хотя в Protocol ниже не типизирована структурно (meta: dict у Candidate
остаётся широким для гибкости; форма — дисциплина реализации, не типов):
  kind=wine:   {"source": {...}, "derived": {...}}   — блоки карточки vines целиком
  kind=chunk:  {"article_id", "title", "heading", "rubric"}
  kind=winery: {"source": {...}}
MockRetriever.get_by_id/search/resolve_label/similar/analog_for_style/
candidates_for_taste — все используют один построитель Candidate на kind,
поэтому meta одинаковая форма везде (см. mock.py::_wine_to_candidate/
_chunk_to_candidate).

candidates_for_taste — сигнатура нормативна с v0.3: позиционный
`exclude_ids: list[str]`, `limit: int = 20`, БЕЗ keyword-only `*` (было
расхождение с v0.2.2, где B держал её keyword-only с Optional/set — приведено
к контракту и к тому, что уже реализовал агент A в packages/rag/rag/base.py).

v0.3.3 (ревью 03, блокер заморозки): `search()` `collections=None` —
ДЕФОЛТ и НОРМА, включает intent-роутинг внутри search() (pairing-запросы
приоритезируют wines над knowledge). Явный tuple — принудительный выбор
коллекций для специальных вызовов, качество выдачи тогда на вызывающем.
Раньше здесь (и в app/chat/service.py) был захардкожен явный дефолт
`("wines", "knowledge")`, который молча обходил intent-роутинг агента A —
сцена 2 демо через API цитировала статьи знаний вместо вин на wine-pick
вопросы (живой зонд ревьюера). `app/chat/service.py` теперь не передаёт
`collections` вовсе.
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
    meta: dict  # форма по kind — см. докстринг модуля (v0.3)


class Retriever(Protocol):
    def search(
        self,
        query: str,
        *,
        filters: Filters | None = None,
        collections: tuple[str, ...] | None = None,
        top_k: int = 8,
    ) -> list[Candidate]:
        """collections=None (дефолт) — норма, intent-роутинг внутри search()
        (v0.3.3). Явный tuple форсирует набор коллекций, обходя роутинг —
        только для специальных вызовов, не для /chat."""

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

    def candidates_for_taste(self, exclude_ids: list[str], limit: int = 20) -> list[Candidate]:
        """Колода для GET /taste/candidates: разнообразие по цвету/региону/
        стилю, исключая exclude_ids. Позиционный exclude_ids — нормативно
        (v0.3), не keyword-only."""


def get_retriever() -> Retriever:  # pragma: no cover - см. app/rag/factory.py
    """Сигнатура из contracts/rag-interface.md v0.2.1: фабрика БЕЗ аргументов,
    симметрично packages/llm.get_llm(). Здесь не реализована — это подпись
    ожидаемой точки входа настоящего packages/rag, вызываемой из
    app/rag/factory.py при RAG_PROVIDER=real. apps/api вместо неё использует
    свою app.rag.factory.get_retriever(settings), которая либо строит
    MockRetriever, либо делегирует в эту функцию из настоящего пакета rag.
    """
    raise NotImplementedError("реализация — в пакете packages/rag, не здесь")
