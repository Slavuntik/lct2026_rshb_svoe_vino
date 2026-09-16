"""app/cv/factory.py — переключение mock|real и прогрев (v0.4.4, ревью 04,
блокер 2). IMAGE_PROVIDER=real/VERIFIER_PROVIDER=real без установленного
packages/cv намеренно не тестируются здесь byte-for-byte (см.
tests/test_integration_real_cv.py) — только честные ошибки факторки и логика
warm_up_image_index(), которая от реального пакета не зависит."""
from __future__ import annotations

import dataclasses

import pytest

from app.config import Settings
from app.cv.factory import get_image_index, get_label_verifier, warm_up_image_index
from app.cv.mock import MockImageIndex, MockLabelVerifier


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


def test_get_image_index_mock_provider_returns_mock():
    assert isinstance(get_image_index(_settings(image_provider="mock")), MockImageIndex)


def test_get_image_index_unknown_provider_raises_value_error():
    with pytest.raises(ValueError):
        get_image_index(_settings(image_provider="bogus"))


def test_get_label_verifier_mock_provider_returns_mock():
    assert isinstance(get_label_verifier(_settings(verifier_provider="mock")), MockLabelVerifier)


def test_get_label_verifier_real_provider_honest_runtime_error_before_g_ships():
    """v0.4.4: packages/cv/cv/verify.py ещё не закоммичен (проверено — файла
    нет) — VERIFIER_PROVIDER=real обязан честно упасть, не притвориться моком."""
    with pytest.raises(RuntimeError):
        get_label_verifier(_settings(verifier_provider="real"))


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
