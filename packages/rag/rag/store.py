"""Тонкая обёртка над Qdrant (qdrant-client) — embedded или сетевой режим.

Docker на dev-машине нет — по умолчанию используем локальный (embedded) режим
qdrant-client (path=...), он подтверждённо работает (upsert/query без сервера,
см. отчёт). В проде Qdrant поднят отдельным сервисом (compose) — если задан
env QDRANT_URL, подключаемся по сети (QdrantClient(url=...)), embedded-путь
в этом случае не используется. Файловый fallback сознательно НЕ реализован:
бриф и контракт v0.2 прямо говорят его не писать превентивно, только если
embedded-режим реально не поднимется — на этой машине он поднимается.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterable

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from rag import config
from rag import refdata

_NAMESPACE = uuid.UUID("2f3f6f0a-6e58-4a5e-9c1f-9a1c2b6a2b10")  # фиксированный namespace для проекта


def point_id(string_id: str) -> str:
    """Стабильный UUID из строкового id — обеспечивает идемпотентный upsert
    (повторный ingest того же id перезаписывает ту же точку, не дублирует)."""
    return str(uuid.uuid5(_NAMESPACE, string_id))


class QdrantStore:
    def __init__(self, path: Path | None = None, url: str | None = None, api_key: str | None = None):
        self.path = path or config.QDRANT_PATH
        self.url = url if url is not None else config.QDRANT_URL
        self.api_key = api_key if api_key is not None else config.QDRANT_API_KEY
        if not self.url:
            self.path.mkdir(parents=True, exist_ok=True)
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            # Прод (compose): сетевой Qdrant по QDRANT_URL. Дев-машина без
            # Docker: embedded-режим по локальному path (см. модуль-докстринг).
            if self.url:
                self._client = QdrantClient(url=self.url, api_key=self.api_key)
            else:
                self._client = QdrantClient(path=str(self.path))
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def ensure_collection(self, name: str, dim: int) -> None:
        if self.client.collection_exists(name):
            info = self.client.get_collection(name)
            existing_dim = info.config.params.vectors.size
            if existing_dim == dim:
                return
            # Модель эмбеддинга сменилась (другая размерность) — пересоздаём.
            self.client.delete_collection(name)
        self.client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

    def upsert(
        self,
        name: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        batch_size: int = 256,
    ) -> None:
        points = [
            PointStruct(id=point_id(sid), vector=vec, payload={**payload, "id": sid})
            for sid, vec, payload in zip(ids, vectors, payloads)
        ]
        for i in range(0, len(points), batch_size):
            self.client.upsert(collection_name=name, points=points[i : i + batch_size])

    def count(self, name: str) -> int:
        if not self.client.collection_exists(name):
            return 0
        return self.client.count(collection_name=name, exact=True).count

    def get_vector(self, name: str, string_id: str) -> list[float] | None:
        pts = self.client.retrieve(
            collection_name=name, ids=[point_id(string_id)], with_vectors=True
        )
        if not pts:
            return None
        return pts[0].vector

    def search(
        self,
        name: str,
        vector: list[float],
        top_k: int,
        query_filter: Filter | None = None,
    ) -> list[tuple[str, float, dict]]:
        if not self.client.collection_exists(name):
            return []
        res = self.client.query_points(
            collection_name=name,
            query=vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        out = []
        for p in res.points:
            payload = p.payload or {}
            out.append((payload.get("id", str(p.id)), float(p.score), payload))
        return out


def build_filter(filters, *, include_region: bool = True, include_wine_fields: bool = True) -> Filter | None:
    """Строит Qdrant Filter из dataclass Filters. `include_wine_fields=False`
    отключает color/sugar/grapes/stillness (используется для коллекции
    wineries, где этих полей в payload нет — только region)."""
    if filters is None:
        return None
    must: list[FieldCondition] = []
    if include_region and filters.region:
        region_slug = refdata.normalize_region(filters.region)
        must.append(FieldCondition(key="filters.region", match=MatchValue(value=region_slug)))
    if include_wine_fields:
        if filters.color:
            must.append(FieldCondition(key="filters.color", match=MatchValue(value=filters.color)))
        if filters.sugar:
            must.append(FieldCondition(key="filters.sugar", match=MatchValue(value=filters.sugar)))
        if filters.grapes:
            must.append(FieldCondition(key="filters.grapes", match=MatchAny(any=list(filters.grapes))))
        if filters.stillness:
            must.append(FieldCondition(key="filters.stillness", match=MatchValue(value=filters.stillness)))
    if not must:
        return None
    return Filter(must=must)
