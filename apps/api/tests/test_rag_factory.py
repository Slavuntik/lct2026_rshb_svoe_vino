"""app/rag/factory.py::warm_up_retriever (reports/backend-chat-retrieval.md,
22.09, п.6, симметрично app/cv/factory.py::warm_up_image_index/
warm_up_label_verifier, см. tests/test_cv_factory.py — тот же паттерн трёх
двойников retriever'а: успех / исключение / "не должен быть вызван вовсе").
RAG_PROVIDER=real с настоящим packages/rag здесь намеренно не тестируется
(тяжёлые ML-зависимости, отдельная интеграция — см. tests/
test_integration_real_rag.py) — только честная логика самой warm_up_retriever()."""
from __future__ import annotations

import dataclasses

from app.config import Settings
from app.rag.factory import warm_up_retriever


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


class _SearchOK:
    def search(self, query: str, *, top_k: int = 1) -> list:
        return []


class _SearchExplodes:
    def search(self, query: str, *, top_k: int = 1) -> list:
        raise RuntimeError("эмбеддер/индекс недоступны — ровно то, что warm-up обязан пережить")


class _SearchMustNotBeCalled:
    def search(self, query: str, *, top_k: int = 1) -> list:
        raise AssertionError("mock-провайдер не должен вызывать search() при прогреве вообще")


def test_warm_up_skips_search_entirely_on_mock_provider():
    """RAG_PROVIDER=mock — нечего греть, True без обращения к search()."""
    assert warm_up_retriever(_SearchMustNotBeCalled(), _settings(rag_provider="mock")) is True


def test_warm_up_returns_true_when_real_retriever_searches_successfully():
    assert warm_up_retriever(_SearchOK(), _settings(rag_provider="real")) is True


def test_warm_up_returns_false_without_raising_when_real_retriever_search_fails():
    """Ошибка прогрева не должна ронять вызывающий код (app/main.py::create_app)
    — лучше поднятый процесс с холодным первым чатом, чем не поднятый вовсе."""
    assert warm_up_retriever(_SearchExplodes(), _settings(rag_provider="real")) is False


def test_warm_up_calls_search_with_nonempty_query_and_small_top_k():
    """Пустая строка/top_k=0 рисковали бы не долететь до реального ветвления
    поиска (см. rag/intent.py::classify — пустой query -> "default" без
    похода в hybrid.search()) — прогрев обязан реально дойти до эмбеддера."""
    calls: list[tuple[str, int]] = []

    class _Spy:
        def search(self, query: str, *, top_k: int = 1) -> list:
            calls.append((query, top_k))
            return []

    assert warm_up_retriever(_Spy(), _settings(rag_provider="real")) is True
    assert len(calls) == 1
    query, top_k = calls[0]
    assert query.strip(), "прогрев обязан передать непустой запрос"
    assert top_k >= 1
