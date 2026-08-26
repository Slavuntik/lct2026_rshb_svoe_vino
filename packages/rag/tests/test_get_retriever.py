"""get_retriever() — фабрика по env RAG_MODE=qdrant|embedded (+QDRANT_URL),
симметрично packages/llm.get_llm (v0.2.1). Только выбор режима — без реальных
сетевых интеграционных тестов (RAG_MODE=qdrant с фейковым URL не должен
пытаться коннектиться на этапе конструирования — Qdrant-клиент ленивый)."""
from __future__ import annotations

import pytest

from rag import config
from rag.base import get_retriever


def test_default_mode_is_embedded(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_MODE", raising=False)
    r = get_retriever(data_dir=tmp_path)
    assert r.store.url is None


def test_explicit_embedded_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_MODE", "embedded")
    r = get_retriever(data_dir=tmp_path)
    assert r.store.url is None


def test_embedded_mode_ignores_stray_qdrant_url(monkeypatch, tmp_path):
    # RAG_MODE=embedded должен форсировать локальный path, даже если в
    # окружении случайно завалялся QDRANT_URL (например, от прод-конфига).
    monkeypatch.setenv("RAG_MODE", "embedded")
    monkeypatch.setattr(config, "QDRANT_URL", "http://qdrant:6333")
    r = get_retriever(data_dir=tmp_path)
    assert r.store.url is None


def test_qdrant_mode_requires_url(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_MODE", "qdrant")
    monkeypatch.setattr(config, "QDRANT_URL", None)
    with pytest.raises(RuntimeError, match="QDRANT_URL"):
        get_retriever(data_dir=tmp_path)


def test_qdrant_mode_with_url_selects_network_store(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_MODE", "qdrant")
    monkeypatch.setattr(config, "QDRANT_URL", "http://qdrant:6333")
    r = get_retriever(data_dir=tmp_path)
    assert r.store.url == "http://qdrant:6333"


def test_unknown_mode_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_MODE", "something-else")
    with pytest.raises(ValueError, match="RAG_MODE"):
        get_retriever(data_dir=tmp_path)
