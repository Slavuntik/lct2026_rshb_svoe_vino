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


def warm_up_retriever(retriever: Retriever, settings: Settings) -> bool:
    """Один холостой search() СРАЗУ при старте процесса — симметрично
    warm_up_image_index()/warm_up_label_verifier() (app/cv/factory.py, ревью 04
    блокер 2 / ревью 05 TODO-1). Дополнение тимлида 22.09 (reports/
    backend-chat-retrieval.md, п.6): на стенде эмбеддер fastembed (dense-модель
    packages/rag/rag/embeddings.py::DenseEmbedder) грузится ЛЕНИВО на первый
    боевой search() — ~8 с на тех же 4 vCPU, что и сканер. Без прогрева это
    совпадает по времени с прогонами 100 фото и даёт им хвост 7-9 с
    (reports/devops-hack-v13.md: p50/p95/max 4.4/6.6/9.1 с). Прогревается
    ТОЛЬКО при RAG_PROVIDER=real (мок мгновенный, нечего греть — то же правило,
    что у image_index/label_verifier). Ошибка прогрева НЕ роняет старт (лучше
    поднятый процесс с холодным первым чатом, чем не поднятый вовсе).

    Осознанно НЕ пишет в app.state.*_warm, которое участвует в GET /healthz.warm
    (app/routers/health.py) — прямое указание тимлида ("healthz.warm трогать не
    нужно"): чат и сканер разные бюджеты/сцены демо, смешивать их AND'ом
    незачем. Время прогрева логирует вызывающий (app/main.py), не эта функция —
    здесь только факт успеха/неудачи."""
    if settings.rag_provider != "real":
        return True
    try:
        retriever.search("вино", top_k=1)
        return True
    except Exception:
        return False
