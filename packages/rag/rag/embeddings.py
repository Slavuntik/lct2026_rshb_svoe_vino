"""Dense-эмбеддер (fastembed) и sparse BM25-индекс (rank_bm25).

Обе части — чисто локальные, без обращений к внешним LLM. Модель dense
эмбеддинга и её выбор задокументированы в rag/config.py и в отчёте.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Iterable

from rag import config
from rag.textutil import tokenize


class DenseEmbedder:
    """Обёртка над fastembed.TextEmbedding. Ленивая инициализация модели."""

    def __init__(self, model_name: str | None = None, cache_dir: Path | None = None):
        self.model_name = model_name or config.DENSE_MODEL_NAME
        self.cache_dir = cache_dir or config.FASTEMBED_CACHE_DIR
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(
                model_name=self.model_name, cache_dir=str(self.cache_dir), threads=config.ONNX_THREADS
            )
        return self._model

    def embed_documents(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        model = self._ensure_loaded()
        if not texts:
            return []
        return [v.tolist() for v in model.embed(texts, batch_size=batch_size)]

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure_loaded()
        # fastembed применяет модель-специфичный query-префикс, если он определён
        # для модели (симметричные модели вроде paraphrase-multilingual-MiniLM
        # ведут себя как embed()); единообразно используем query_embed.
        return list(model.query_embed(text))[0].tolist()


class BM25Index:
    """Sparse BM25 поверх rank_bm25 — тот же интерфейс что бы ни было хранилищем
    векторов (embedded Qdrant или гипотетический файловый fallback)."""

    def __init__(self, ids: list[str], bm25):
        self.ids = ids
        self._bm25 = bm25

    @classmethod
    def build(cls, ids: list[str], texts: list[str]) -> "BM25Index":
        from rank_bm25 import BM25Okapi

        tokenized = [tokenize(t) for t in texts]
        # rank_bm25 не переживает полностью пустой корпус токенов
        if not tokenized:
            tokenized = [[]]
            ids = ids or [""]
        bm25 = BM25Okapi(tokenized)
        return cls(ids=list(ids), bm25=bm25)

    def scores(self, query: str) -> dict[str, float]:
        q_tokens = tokenize(query)
        if not q_tokens or not self.ids:
            return {}
        raw = self._bm25.get_scores(q_tokens)
        return dict(zip(self.ids, raw))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"ids": self.ids, "bm25": self._bm25}, f)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with open(path, "rb") as f:
            data = pickle.load(f)
        return cls(ids=data["ids"], bm25=data["bm25"])
