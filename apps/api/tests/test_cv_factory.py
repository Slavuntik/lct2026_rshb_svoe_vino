"""app/cv/factory.py — переключение mock|real и прогрев (v0.4.4, ревью 04,
блокер 2). IMAGE_PROVIDER=real/VERIFIER_PROVIDER=real byte-for-byte поведение
реального пайплайна (embed/search/OCR) намеренно не тестируется здесь (см.
tests/test_integration_real_cv.py) — только честные ошибки факторки и логика
warm_up_image_index(), которая от реального пакета не зависит. Единственное
исключение — test_get_label_verifier_real_provider_returns_real_cv_verifier_
when_installed ниже: конструктор LabelVerifier() дешёвый (PaddleOCR грузится
ЛЕНИВО на первый verify(), не в __init__), поэтому isinstance-проверку можно
себе позволить и вне тяжёлого RUN_CV_INTEGRATION-гейта — сама себя скипает,
если packages/cv не установлен."""
from __future__ import annotations

import dataclasses

import pytest

from app.config import Settings
from app.cv.factory import get_image_index, get_label_verifier, warm_up_image_index
from app.cv.mock import MockImageIndex, MockLabelVerifier

try:
    import cv.verify as _cv_verify_probe  # noqa: F401 — наличие проверяем самим импортом
    _CV_VERIFY_IMPORTABLE = True
except ImportError:
    _CV_VERIFY_IMPORTABLE = False


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


def test_get_image_index_mock_provider_returns_mock():
    assert isinstance(get_image_index(_settings(image_provider="mock")), MockImageIndex)


def test_get_image_index_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        get_image_index(_settings(image_provider="bogus"))


def test_get_label_verifier_mock_provider_returns_mock():
    assert isinstance(get_label_verifier(_settings(verifier_provider="mock")), MockLabelVerifier)


@pytest.mark.skipif(not _CV_VERIFY_IMPORTABLE, reason="packages/cv не установлен (uv sync --extra integration)")
def test_get_label_verifier_real_provider_returns_real_cv_verifier_when_installed():
    """v0.4.4 (агент G, коммит 6a7e47a): packages/cv/cv/verify.py сдан и
    импортируется — VERIFIER_PROVIDER=real больше не обязан быть
    RuntimeError-заглушкой (была таковой до её коммита), а конструирует
    настоящий cv.verify.LabelVerifier() напрямую (тот же паттерн, что и
    get_image_index() для cv.index.ImageIndex — нет фабричной функции в
    пакете, класс без обязательных аргументов)."""
    verifier = get_label_verifier(_settings(verifier_provider="real"))
    assert isinstance(verifier, _cv_verify_probe.LabelVerifier)


def test_get_label_verifier_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        get_label_verifier(_settings(verifier_provider="bogus"))


class _EmbedOK:
    def embed(self, image: bytes) -> list[float]:
        return [0.0]


class _EmbedExplodes:
    def embed(self, image: bytes) -> list[float]:
        raise RuntimeError("энкодер недоступен — ровно то, что warm-up обязан пережить")


class _EmbedMustNotBeCalled:
    def embed(self, image: bytes) -> list[float]:
        raise AssertionError("mock-провайдер не должен вызывать embed() при прогреве вообще")


def test_warm_up_skips_embed_entirely_on_mock_provider():
    """IMAGE_PROVIDER=mock — нечего греть, True без обращения к embed()."""
    assert warm_up_image_index(_EmbedMustNotBeCalled(), _settings(image_provider="mock")) is True


def test_warm_up_returns_true_when_real_encoder_embeds_successfully():
    assert warm_up_image_index(_EmbedOK(), _settings(image_provider="real")) is True


def test_warm_up_returns_false_without_raising_when_real_encoder_fails():
    """Ошибка прогрева не должна ронять вызывающий код (app/main.py::create_app)
    — лучше поднятый процесс с warm=False, чем не поднятый вовсе."""
    assert warm_up_image_index(_EmbedExplodes(), _settings(image_provider="real")) is False
