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
```

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
