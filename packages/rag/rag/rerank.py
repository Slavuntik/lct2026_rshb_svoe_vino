"""Реранкер top-N кандидатов кросс-энкодером. Опциональная стадия пайплайна.

bge-reranker-v2-m3 (из контракта) недоступен в установленной версии fastembed
(0.8.0не в реестре TextCrossEncoder.list_supported_models()). Используем
мультиязычную альтернативу jinaai/jina-reranker-v2-base-multilingual —
подробности выбора в rag/config.py и в отчёте. Если модель недоступна
(нет сети/кэша на конкретной машине) — тихо откатываемся на NoOpReranker,
чтобы retrieval продолжал работать в режиме dense+BM25 (RRF) без реранка,
как явно разрешено брифом.
"""
from __future__ import annotations

import logging
from typing import Protocol

from rag import config

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def rerank(self, query: str, docs: list[str]) -> list[float]:
        """Возвращает скор для каждого docs[i] относительно query (выше = релевантнее)."""
        ...


class NoOpReranker:
    """Пайплайн без реранка: сохраняет порядок/скор, пришедший из RRF-слияния."""

    name = "none"

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        # Возвращаем убывающую последовательность, чтобы сортировка по score
        # не переставляла уже отранжированные RRF кандидаты.
        n = len(docs)
        return [float(n - i) for i in range(n)]


class CrossEncoderReranker:
    def __init__(self, model_name: str):
        self.name = model_name
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            config.FASTEMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._model = TextCrossEncoder(model_name=self.name, cache_dir=str(config.FASTEMBED_CACHE_DIR))
        return self._model

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        try:
            model = self._ensure_loaded()
            return [float(s) for s in model.rerank(query, docs)]
        except Exception as exc:  # noqa: BLE001 — намеренно широкий catch: сеть,
            # отсутствующий кэш модели или битые веса не должны ронять retrieval —
            # откатываемся на порядок RRF (см. NoOpReranker), как разрешает бриф.
            logger.warning("Реранкер %s недоступен (%s) — отдаём порядок RRF без реранка.", self.name, exc)
            n = len(docs)
            return [float(n - i) for i in range(n)]


def build_reranker(model_name: str | None = None) -> Reranker:
    """Фабрика. Загрузка модели ЛЕНИВАЯ (при первом .rerank()), чтобы создание
    Retriever не платило за скачивание/инициализацию модели тем вызовам
    (resolve_label/resolve_style/analog_for_style), которым реранкер не нужен."""
    name = model_name if model_name is not None else config.RERANKER_MODEL_NAME
    if not name:
        return NoOpReranker()
    return CrossEncoderReranker(name)
