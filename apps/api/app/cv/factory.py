"""Фабрики ImageIndex/LabelVerifier по env (симметрично rag/factory.py и
packages/llm.get_llm()).

IMAGE_PROVIDER не задан или "mock" => MockImageIndex (фикстуры, без сети/моделей).
IMAGE_PROVIDER=real => настоящий packages/cv агента G (коммит 4fdb445 — сдан,
ImageIndex по контракту, qdrant_embedded, self-match 93.9%). Сверено
посимвольно с `cv/index.py` (задание оркестратора после урока ревью 02: "оба
берега хороши, пока швы не примерены"):
  - `Match(slug: str, score: float, gap: float | None, view: str)` — поля и
    типы идентичны app/cv/interface.py::Match; `view` использует латинское
    "real", НЕ "реальный" — контракт v0.4 сам ошибался в прозе ("реальный
    | synth-N"), v0.4.2 это поправил, MockImageIndex тоже приведён (было
    расхождение — см. reports/b-report.md).
  - Фабричной функции в пакете НЕТ (в отличие от packages/rag.get_retriever())
    — только класс `cv.index.ImageIndex`, конструктор без обязательных
    аргументов (`store`/`encoder`/`collection` — все опциональны, читают env
    сами через cv/config.py). Импорт — ИМЕННО `cv.index` (не просто `cv`,
    там пока только `__version__`), как явно указал оркестратор.
IMAGE_INDEX_MODE (qdrant_embedded|pgvector, contracts/image-scan.md) — это уже
ВНУТРЕННИЙ выбор бэкенда настоящего ImageIndex (cv/config.py), не имеет
отношения к этому файлу.

LABEL_VERIFIER_PROVIDER аналогично для LabelVerifier — реализация OCR-
верификатора вне зоны B и вне зоны G ("реализация придёт позже"), дефолт
по-прежнему mock (эта волна её не касается).
"""
from __future__ import annotations

from ..config import Settings
from .interface import ImageIndex, LabelVerifier
from .mock import MockImageIndex, MockLabelVerifier


def get_image_index(settings: Settings) -> ImageIndex:
    provider = settings.image_provider
    if provider == "mock":
        return MockImageIndex()
    if provider == "real":
        try:
            from cv import index as _cv_index  # packages/cv/cv/index.py, зона агента G
        except ImportError as exc:
            raise RuntimeError(
                "IMAGE_PROVIDER=real, но пакет packages/cv не установлен в это "
                "окружение (uv pip install -e ../../packages/cv, или "
                "uv sync --extra integration в apps/api). Пока он не нужен — "
                "используйте IMAGE_PROVIDER=mock (дефолт)."
            ) from exc
        if hasattr(_cv_index, "get_image_index"):
            return _cv_index.get_image_index()
        if hasattr(_cv_index, "ImageIndex"):
            return _cv_index.ImageIndex()
        raise RuntimeError(
            "packages/cv установлен, но cv.index не предоставляет ни "
            "get_image_index(), ни класс ImageIndex() без аргументов — "
            "согласуйте способ инстанцирования с агентом G."
        )
    raise ValueError(f"Неизвестный IMAGE_PROVIDER={provider!r}, ожидается mock|real")


def get_label_verifier(settings: Settings) -> LabelVerifier:
    provider = settings.label_verifier_provider
    if provider == "mock":
        return MockLabelVerifier()
    if provider == "real":
        raise RuntimeError(
            "LABEL_VERIFIER_PROVIDER=real, но реализация OCR-верификатора ещё "
            "не готова (вне зоны B и вне зоны G — придёт позже). Используйте "
            "LABEL_VERIFIER_PROVIDER=mock (дефолт)."
        )
    raise ValueError(
        f"Неизвестный LABEL_VERIFIER_PROVIDER={provider!r}, ожидается mock|real"
    )
