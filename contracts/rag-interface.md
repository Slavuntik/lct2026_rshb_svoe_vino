# Контракт RAG-сервиса (packages/rag) v0.1 — ЗАМОРОЖЕН

Данные: каталог vines, read-only:
`/Users/vyacheslavfokin/ClaudeWorkspace/vines/catalog/` (wines/, wineries/, articles/),
`/Users/vyacheslavfokin/ClaudeWorkspace/vines/build/` (index.jsonl, wineries.jsonl, articles.jsonl),
`/Users/vyacheslavfokin/ClaudeWorkspace/vines/ref/` (справочники, reference_styles.yaml).

## Интерфейс

```python
# packages/rag/rag/base.py
from dataclasses import dataclass, field

@dataclass
class Filters:
    color: str | None = None
    sugar: str | None = None
    region: str | None = None
    grapes: list[str] = field(default_factory=list)
    stillness: str | None = None          # тихое | игристое

@dataclass
class Candidate:
    id: str                                # wine slug | "article:<slug>#<n>" | "winery:<slug>"
    kind: str                              # wine | chunk | winery
    score: float
    text: str                              # текст для промпта
    url: str                               # первоисточник для цитаты
    meta: dict

class Retriever:
    def search(self, query: str, *, filters: Filters | None = None,
               collections: tuple[str, ...] = ("wines", "knowledge"),
               top_k: int = 8) -> list[Candidate]: ...
    def resolve_label(self, text: str, hints: dict | None = None) -> list[Candidate]:
        """Для /scan/resolve: fuzzy по name+winery_name (rapidfuzz), НЕ векторный поиск."""
    def similar(self, wine_id: str, top_k: int = 6) -> list[Candidate]: ...
    def analog_for_style(self, style_slug: str, *, filters: Filters | None = None,
                         top_k: int = 12) -> list[Candidate]:
        """«Аналог импортного»: reference_style -> вина с этим стилем в derived."""
    def resolve_style(self, query: str) -> dict | None:
        """«люблю Просекко» -> {"slug": "prosecco", "name": "Просекко", "country": "Италия"}.
        Fuzzy по name/slug/синонимам из ref/reference_styles.yaml; None, если не распознан.
        Добавлено v0.2 (ревью 01, блокер 3)."""
    def get_by_id(self, id: str) -> Candidate | None:
        """Карточка по id (wine-slug | article:<slug>#<n> | winery:<slug>) — для /wines/{id}.
        Добавлено v0.2.1 (предложение агента B)."""
    def list_reference_styles(self, top_n: int = 5) -> list[dict]:
        """Популярные стили ({"slug","name","country"}) — подсказка в 404 /analogs. v0.2.1."""
    def candidates_for_taste(self, exclude_ids: list[str], limit: int = 20) -> list[Candidate]:
        """Колода для свайп-дегустации: разнообразие по цвету/региону/стилю, исключая
        exclude_ids. Детерминированной случайности достаточно (seed по дате).
        Добавлено v0.2.3 (предложение агента B: метод жил только в моке —
        без контракта интеграция реального RAG сломала бы /taste/candidates)."""

def get_retriever() -> Retriever: ...
# Фабрика по env, симметрично packages/llm.get_llm: RAG_MODE=qdrant|embedded (+QDRANT_URL). v0.2.1
```

## Уточнения v0.2.4 (по отчёту A, докс-онли — код не меняется)

- **Candidate.score сравним только внутри одного ответа одного метода.** Природа скора
  различается (RRF-доля, логит кросс-энкодера — бывает отрицательным, rapidfuzz/100,
  1/(1+dist)); сравнивать между вызовами или методами — баг.
- **resolve_label:** «синонимы» = сортовые синонимы из `ref/grape_synonyms.yaml`;
  алиасов производителей в данных vines нет — при необходимости это отдельный источник.
- **Filters.sugar (одно значение)** — фильтр по фактическому сахару вина; список
  `sugar` в reference_styles.yaml — допустимые категории при матче стиля. Две разные
  проверки, не унифицировать.
- **Свободный текст — слабый режим для «pick»-запросов:** извлечение Filters из реплики
  до вызова search() — ответственность вызывающего (/chat, волна 3), не ретривера.

## Пайплайн поиска (по канонам)

фильтры (жёсткие, до векторов) → sparse BM25 + dense (bge-m3, локально, кэш эмбеддингов)
→ RRF-слияние → реранкер bge-reranker-v2-m3 top-50 → top-8.
Векторное хранилище: Qdrant, коллекции `wines`, `knowledge`, `wineries`;
payload вина = `filters`-блок из index.jsonl **плюс** `stillness` и
`reference_style_matches` из derived (в filters-блоке их нет — уточнение v0.2 по ревью 01,
блокер 6; без них не работают Filters.stillness и analog_for_style).
Embedded-режим qdrant-client (path=...) — основной для разработки, Docker не нужен;
файловый fallback НЕ писать превентивно — только если embedded реально не заведётся.

## Индексация и версии

CLI: `rag ingest --source <vines_build_dir> --version <YYYYMMDD.N>` — идемпотентно.
`rag eval --goldset eval/goldset.jsonl` — метрики в stdout + `eval/report.json`.
Версия индекса отдаётся в `/healthz` через API.

## Голд-сет

`eval/goldset.jsonl`, строка: `{"q": "...", "type": "pick|analog|pairing|fact|travel",
"expect_ids": ["..."], "expect_any": true}` — минимум 60 вопросов, распределённых по типам,
сгенерированных из реальных данных каталога (проверить руками — вопросы должны быть честными).

## DoD-метрики

hit@8 ≥ 0.85 на голд-сете; p95 retrieval ≤ 300 мс на этой машине (без реранкера — замерить оба);
ingest всего каталога ≤ 10 мин.
