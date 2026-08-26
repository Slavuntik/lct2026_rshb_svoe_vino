"""Фабрика ретривера по env RAG_PROVIDER (симметрично packages/llm.get_llm()).

RAG_PROVIDER не задан или "mock" => MockRetriever (фикстуры, без сети/моделей).
RAG_PROVIDER=real => попытка подключить настоящий packages/rag агента A.
packages/rag на момент написания кода ещё не реализован (агент A работает
параллельно) — предложение к контракту (см. reports/b-report.md): пакету rag
стоит завести симметричный get_retriever() фабричный метод в rag/base.py, как
уже сделано в packages/llm/llm/base.py. Пока такого метода нет, здесь есть
запасной путь через прямое имя класса Retriever, с понятной ошибкой, если
ничего не подошло.
"""
from __future__ import annotations

from ..config import Settings
from .interface import Retriever
from .mock import MockRetriever


def get_retriever(settings: Settings) -> Retriever:
    provider = settings.rag_provider
    if provider == "mock":
        return MockRetriever()
    if provider == "real":
        try:
            import rag as _rag_pkg  # packages/rag, зона агента A
        except ImportError as exc:
            raise RuntimeError(
                "RAG_PROVIDER=real, но пакет packages/rag не установлен в это "
                "окружение (uv pip install -e ../../packages/rag). Пока он не "
                "готов — используйте RAG_PROVIDER=mock (дефолт)."
            ) from exc
        if hasattr(_rag_pkg, "get_retriever"):
            return _rag_pkg.get_retriever()
        if hasattr(_rag_pkg, "Retriever"):
            return _rag_pkg.Retriever()  # type: ignore[call-arg]
        raise RuntimeError(
            "packages/rag установлен, но не предоставляет ни get_retriever(), "
            "ни класс Retriever() без аргументов — согласуйте способ "
            "инстанцирования с агентом A (см. предложения к контракту в "
            "reports/b-report.md)."
        )
    raise ValueError(f"Неизвестный RAG_PROVIDER={provider!r}, ожидается mock|real")
