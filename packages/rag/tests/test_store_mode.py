"""Выбор режима подключения QdrantStore: embedded (path=, dev-дефолт, Docker
нет) против сетевого (url=, прод — Qdrant отдельным сервисом в compose).

Только выбор конструктора — без реальных сетевых интеграционных тестов
(так и попросил оркестратор): не обращаемся к client, чтобы не пытаться
реально коннектиться к несуществующему хосту.
"""
from __future__ import annotations

from rag.store import QdrantStore


def test_defaults_to_embedded_path_when_no_url(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    assert store.url is None
    assert store.path == tmp_path / "qdrant"
    assert store.path.exists()  # embedded-режим создаёт директорию сразу


def test_explicit_url_selects_network_mode(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant", url="http://localhost:6333")
    assert store.url == "http://localhost:6333"
    # в сетевом режиме локальную директорию заводить незачем
    assert not store.path.exists()


def test_env_qdrant_url_selects_network_mode(tmp_path, monkeypatch):
    from rag import config

    monkeypatch.setattr(config, "QDRANT_URL", "http://qdrant:6333")
    store = QdrantStore(path=tmp_path / "qdrant")  # url не передан явно -> берётся из config
    assert store.url == "http://qdrant:6333"


def test_no_env_no_arg_is_embedded(tmp_path, monkeypatch):
    from rag import config

    monkeypatch.setattr(config, "QDRANT_URL", None)
    store = QdrantStore(path=tmp_path / "qdrant")
    assert store.url is None
