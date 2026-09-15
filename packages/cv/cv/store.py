"""Тонкая обёртка над Qdrant (qdrant-client) — cv/store.py.

Контракт (image-scan.md): `IMAGE_INDEX_MODE=qdrant_embedded | pgvector`. Реализован
только qdrant_embedded — pgvector контракт сам называет "прод-профилем в compose"
(Docker на этой машине нет, вне бюджета этой части задачи). `QdrantStore` зеркалит
packages/rag/rag/store.py (тот же паттерн: embedded `path=` — дев-дефолт без Docker,
сетевой `url=` — прод); интерфейс один, "переезд на прод Qdrant" — строчка env,
без изменений в `cv/index.py`.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from cv import config

_NAMESPACE = uuid.UUID("7e2a8b0e-2f39-4d9a-9d90-6a6d2a4a8e31")  # свой namespace (не путать с packages/rag)


def point_id(string_id: str) -> str:
    """Стабильный UUID из строкового id ("slug::view") — повторный upsert того же
    id перезаписывает ту же точку, не дублирует (идемпотентность build()/add())."""
    return str(uuid.uuid5(_NAMESPACE, string_id))


class QdrantStore:
    def __init__(self, path: Path | None = None, url: str | None = None):
        self.path = path or config.QDRANT_PATH
        self.url = url if url is not None else config.QDRANT_URL
        if not self.url:
            self.path.mkdir(parents=True, exist_ok=True)
        self._client: QdrantClient | None = None

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            if self.url:
                self._client = QdrantClient(url=self.url, api_key=config.QDRANT_API_KEY)
            else:
                self._client = QdrantClient(path=str(self.path))
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def recreate_collection(self, name: str, dim: int) -> None:
        """Пересоздаёт коллекцию с нуля — используется `build()` (полная переиндексация)."""
        if self.client.collection_exists(name):
            self.client.delete_collection(name)
        self.client.create_collection(collection_name=name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def ensure_collection(self, name: str, dim: int) -> None:
        """Создаёт коллекцию, только если её ещё нет — используется `add()` (инкремент
        без ребилда: если коллекция уже есть, ничего не трогаем, просто апсертим)."""
        if self.client.collection_exists(name):
            return
        self.client.create_collection(collection_name=name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def upsert(self, name: str, ids: list[str], vectors: list[list[float]], payloads: list[dict[str, Any]]) -> None:
        if not ids:
            return
        points = [
            PointStruct(id=point_id(i), vector=v, payload={**p, "id": i}) for i, v, p in zip(ids, vectors, payloads)
        ]
        self.client.upsert(collection_name=name, points=points)

    def search(self, name: str, vector: list[float], top_k: int) -> list[tuple[str, float, dict]]:
        if not self.client.collection_exists(name):
            return []
        res = self.client.query_points(collection_name=name, query=vector, limit=top_k, with_payload=True)
        out = []
        for p in res.points:
            payload = p.payload or {}
            out.append((payload.get("id", str(p.id)), float(p.score), payload))
        return out

    def count(self, name: str) -> int:
        if not self.client.collection_exists(name):
            return 0
        return self.client.count(collection_name=name, exact=True).count


def get_store() -> QdrantStore:
    """Фабрика по `config.IMAGE_INDEX_MODE` (контракт: qdrant_embedded | pgvector)."""
    mode = config.IMAGE_INDEX_MODE
    if mode == "qdrant_embedded":
        return QdrantStore()
    if mode == "pgvector":
        raise NotImplementedError(
            "IMAGE_INDEX_MODE=pgvector: прод-бэкенд (compose), вне брифа агента G на этой "
            "машине без Docker. ImageIndex не зависит от бэкенда (см. cv/index.py) — "
            "реализация добавляется без изменений в остальном пакете; см. reports/g-report.md."
        )
    raise ValueError(f"неизвестный IMAGE_INDEX_MODE={mode!r} (ожидается qdrant_embedded|pgvector)")
