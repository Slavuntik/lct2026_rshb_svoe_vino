# packages/rag/rag/base.py
"""Контракт RAG-сервиса (contracts/rag-interface.md, v0.2.1) — реализация.

Filters/Candidate реэкспортированы из rag.types (см. комментарий там про
циклические импорты). Retriever — тонкий фасад, вся логика — в отдельных
модулях: rag.hybrid (search/similar), rag.resolve (resolve_label/resolve_style
— fuzzy, НЕ векторный поиск), rag.styles (analog_for_style/list_reference_styles
— метаданные, без эмбеддингов). Здесь только сборка и загрузка артефактов
ingest'а плюс фабрика get_retriever().
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from rag import config
from rag import resolve as resolve_mod
from rag.embeddings import BM25Index, DenseEmbedder
from rag.hybrid import HybridSearcher
from rag.rerank import build_reranker
from rag.store import QdrantStore
from rag.styles import StyleMatcher
from rag.types import Candidate, Filters

__all__ = ["Filters", "Candidate", "Retriever", "get_retriever"]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class Retriever:
    """Точка входа для B (API): search / resolve_label / similar /
    analog_for_style / resolve_style. Собирается из артефактов последнего
    `rag ingest` (см. rag/ingest.py) — Qdrant embedded store, BM25-пиклы,
    payload-сайдкары, labels-корпус.
    """

    def __init__(self, data_dir: Path | None = None, *, reranker=None, store: QdrantStore | None = None):
        """`reranker`/`store` — точки внедрения для тестов и для get_retriever()
        (например NoOpReranker() и явный embedded/qdrant QdrantStore), чтобы
        юнит-тесты не платили за загрузку кросс-энкодера и не зависели от
        того, закэширована ли модель / поднят ли сетевой Qdrant на машине."""
        self.data_dir = Path(data_dir) if data_dir else config.DATA_DIR
        self._manifest = self._load_manifest()

        self.store = store if store is not None else QdrantStore(path=self.data_dir / "qdrant")
        self.embedder = DenseEmbedder()
        self.reranker = reranker if reranker is not None else build_reranker()

        self.bm25_indexes: dict[str, BM25Index] = {}
        self.payload_by_id: dict[str, dict[str, dict]] = {}
        self.payload_list: dict[str, list[dict]] = {}
        for name in config.COLLECTIONS:
            bm25_path = self.data_dir / "bm25" / f"{name}.pkl"
            if bm25_path.exists():
                self.bm25_indexes[name] = BM25Index.load(bm25_path)
            payloads = _read_jsonl(self.data_dir / "payloads" / f"{name}.jsonl")
            self.payload_list[name] = payloads
            self.payload_by_id[name] = {p["id"]: p for p in payloads}

        self.labels = _read_jsonl(self.data_dir / "labels.jsonl")
        self.style_matcher = StyleMatcher()
        self.hybrid = HybridSearcher(
            store=self.store,
            embedder=self.embedder,
            bm25_indexes=self.bm25_indexes,
            payload_by_id=self.payload_by_id,
            reranker=self.reranker,
        )

    def _load_manifest(self) -> dict | None:
        path = self.data_dir / "manifest.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def index_version(self) -> str | None:
        """Версия индекса последнего ingest — отдаётся API в /healthz."""
        return self._manifest.get("version") if self._manifest else None

    # ------------------------------------------------------------------ #
    # Контракт
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        filters: Filters | None = None,
        collections: tuple[str, ...] = ("wines", "knowledge"),
        top_k: int = 8,
    ) -> list[Candidate]:
        return self.hybrid.search(query, filters=filters, collections=collections, top_k=top_k)

    def resolve_label(self, text: str, hints: dict | None = None) -> list[Candidate]:
        """Для /scan/resolve: fuzzy по name+winery_name(+синонимы сортов),
        rapidfuzz, НЕ векторный поиск."""
        return resolve_mod.resolve_label(text, self.labels, hints=hints)

    def similar(self, wine_id: str, top_k: int = 6) -> list[Candidate]:
        return self.hybrid.similar(wine_id, top_k=top_k)

    def analog_for_style(
        self, style_slug: str, *, filters: Filters | None = None, top_k: int = 12
    ) -> list[Candidate]:
        """«Аналог импортного»: reference_style -> вина с этим стилем в derived."""
        return self.style_matcher.analog_for_style(
            style_slug, self.payload_list.get("wines", []), filters=filters, top_k=top_k
        )

    def resolve_style(self, query: str) -> dict | None:
        """«люблю Просекко» -> {"slug": "prosecco", "name": "Просекко", "country": "Италия"}.
        Fuzzy по name/slug из ref/reference_styles.yaml; None, если не распознан."""
        return self.style_matcher.resolve(query)

    def get_by_id(self, id: str) -> Candidate | None:
        """Карточка по id (wine-slug | article:<slug>#<n> | winery:<slug>) — для
        /wines/{id}. Прямой lookup в payload-кэше, без похода в Qdrant/эмбеддингов.
        v0.2.1 (предложение агента B)."""
        if id.startswith("article:"):
            collection = "knowledge"
        elif id.startswith("winery:"):
            collection = "wineries"
        else:
            collection = "wines"
        payload = self.payload_by_id.get(collection, {}).get(id)
        if payload is None:
            return None
        return Candidate(
            id=id,
            kind=payload.get("kind", collection),
            score=1.0,
            text=payload.get("text", ""),
            url=payload.get("url", ""),
            meta=payload,
        )

    def list_reference_styles(self, top_n: int = 5) -> list[dict]:
        """Популярные стили ({"slug","name","country"}) по частоте в
        filters.reference_style_matches каталога — подсказка в 404 /analogs. v0.2.1."""
        return self.style_matcher.list_popular(self.payload_list.get("wines", []), top_n=top_n)


def get_retriever(data_dir: Path | None = None) -> Retriever:
    """Фабрика по env, симметрично packages/llm.get_llm.

    RAG_MODE=embedded (дефолт) — локальный embedded Qdrant (path=...), Docker
    не нужен, режим для dev-машины. RAG_MODE=qdrant — сетевой Qdrant (прод,
    сервис в compose), требует QDRANT_URL; форсируется явно, даже если рядом
    случайно висит QDRANT_URL, а RAG_MODE не выставлен (дефолт остаётся
    embedded — по умолчанию ничего никуда не коннектится по сети).
    """
    mode = os.environ.get("RAG_MODE", "embedded").strip().lower()
    ddir = Path(data_dir) if data_dir else config.DATA_DIR

    if mode == "embedded":
        store = QdrantStore(path=ddir / "qdrant", url=None)
    elif mode == "qdrant":
        if not config.QDRANT_URL:
            raise RuntimeError("RAG_MODE=qdrant требует переменную окружения QDRANT_URL")
        store = QdrantStore(path=ddir / "qdrant", url=config.QDRANT_URL, api_key=config.QDRANT_API_KEY)
    else:
        raise ValueError(f"RAG_MODE должен быть 'embedded' или 'qdrant', получено {mode!r}")

    return Retriever(data_dir=ddir, store=store)
