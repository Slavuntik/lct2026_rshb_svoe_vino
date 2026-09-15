from __future__ import annotations

import pytest

from cv import config
from cv.store import QdrantStore, get_store


def test_qdrant_embedded_roundtrip(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    store.recreate_collection("c", dim=4)
    assert store.count("c") == 0

    store.upsert("c", ["a", "b"], [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], [{"slug": "a"}, {"slug": "b"}])
    assert store.count("c") == 2

    results = store.search("c", [1.0, 0.0, 0.0, 0.0], top_k=2)
    assert results
    top_id, score, payload = results[0]
    assert top_id == "a"
    assert payload["slug"] == "a"


def test_upsert_same_id_is_idempotent(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    store.recreate_collection("c", dim=2)
    store.upsert("c", ["x"], [[1.0, 0.0]], [{"slug": "x"}])
    store.upsert("c", ["x"], [[0.0, 1.0]], [{"slug": "x"}])  # тот же id — перезапись, не дубль
    assert store.count("c") == 1


def test_search_on_missing_collection_returns_empty(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    assert store.search("does-not-exist", [0.0, 0.0], top_k=5) == []
    assert store.count("does-not-exist") == 0


def test_ensure_collection_is_noop_if_exists(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    store.recreate_collection("c", dim=3)
    store.upsert("c", ["a"], [[1.0, 0.0, 0.0]], [{"slug": "a"}])
    store.ensure_collection("c", dim=3)  # не должно пересоздавать/чистить коллекцию
    assert store.count("c") == 1


def test_get_store_qdrant_embedded(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "IMAGE_INDEX_MODE", "qdrant_embedded")
    monkeypatch.setattr(config, "QDRANT_PATH", tmp_path / "qdrant")  # не трогаем packages/cv/data/ реального пакета
    store = get_store()
    assert isinstance(store, QdrantStore)


def test_get_store_pgvector_not_implemented(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_INDEX_MODE", "pgvector")
    with pytest.raises(NotImplementedError):
        get_store()


def test_get_store_unknown_mode_raises(monkeypatch):
    monkeypatch.setattr(config, "IMAGE_INDEX_MODE", "something-else")
    with pytest.raises(ValueError):
        get_store()
