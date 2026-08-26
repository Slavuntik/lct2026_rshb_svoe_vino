"""Фабрика ретривера по env RAG_PROVIDER (симметрично packages/llm.get_llm()).

RAG_PROVIDER не задан или "mock" => MockRetriever (фикстуры, без сети/моделей).
RAG_PROVIDER=real => подключить настоящий packages/rag агента A через
rag.get_retriever() — эта функция сама стала частью contracts/rag-interface.md
в v0.2.1 (было предложением агента B, символично packages/llm.get_llm()).
На момент написания этого файла packages/rag ещё не реализует ни
get_retriever(), ни (раньше) get_by_id/list_reference_styles — агент A
подхватит v0.2.1 в следующей волне. Пока get_retriever() в пакете нет, здесь
остаётся запасной путь через прямое имя класса Retriever() без аргументов
(текущая реализация A это уже поддерживает), с понятной ошибкой, если и это
не подошло.
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
