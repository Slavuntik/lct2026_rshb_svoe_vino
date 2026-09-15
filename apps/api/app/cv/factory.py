"""Фабрики ImageIndex/LabelVerifier по env (симметрично rag/factory.py и
packages/llm.get_llm()).

CV_PROVIDER не задан или "mock" => MockImageIndex (фикстуры, без сети/моделей).
CV_PROVIDER=real => подключить настоящий packages/cv агента G через
cv.get_image_index() — пакет на момент написания этого кода ещё не
существует вовсе (датасет кейса не приехал); при отсутствии — понятная
ошибка вместо ImportError с середины стека. IMAGE_INDEX_MODE
(qdrant_embedded|pgvector, contracts/image-scan.md) — это уже ВНУТРЕННИЙ
выбор бэкенда настоящего ImageIndex, не имеет отношения к этому файлу.

LABEL_VERIFIER_PROVIDER аналогично для LabelVerifier — реализация OCR-
верификатора вне зоны B ("реализация придёт позже"), дефолт тоже mock.
"""
from __future__ import annotations

from ..config import Settings
from .interface import ImageIndex, LabelVerifier
from .mock import MockImageIndex, MockLabelVerifier


def get_image_index(settings: Settings) -> ImageIndex:
    provider = settings.cv_provider
    if provider == "mock":
        return MockImageIndex()
    if provider == "real":
        try:
            import cv as _cv_pkg  # packages/cv, зона агента G
        except ImportError as exc:
            raise RuntimeError(
                "CV_PROVIDER=real, но пакет packages/cv не установлен в это "
                "окружение (датасет кейса ещё не приехал/пакет не готов). "
                "Пока он не готов — используйте CV_PROVIDER=mock (дефолт)."
            ) from exc
        if hasattr(_cv_pkg, "get_image_index"):
            return _cv_pkg.get_image_index()
        if hasattr(_cv_pkg, "ImageIndex"):
            return _cv_pkg.ImageIndex()  # type: ignore[call-arg]
        raise RuntimeError(
            "packages/cv установлен, но не предоставляет ни get_image_index(), "
            "ни класс ImageIndex() без аргументов — согласуйте способ "
            "инстанцирования с агентом G."
        )
    raise ValueError(f"Неизвестный CV_PROVIDER={provider!r}, ожидается mock|real")


def get_label_verifier(settings: Settings) -> LabelVerifier:
    provider = settings.label_verifier_provider
    if provider == "mock":
        return MockLabelVerifier()
    if provider == "real":
        raise RuntimeError(
            "LABEL_VERIFIER_PROVIDER=real, но реализация OCR-верификатора ещё "
            "не готова (вне зоны B — придёт позже). Используйте "
            "LABEL_VERIFIER_PROVIDER=mock (дефолт)."
        )
    raise ValueError(
        f"Неизвестный LABEL_VERIFIER_PROVIDER={provider!r}, ожидается mock|real"
    )
