"""app/cv/factory.py — прогрев текстовой ветки CV_FUSION при старте (задача
тимлида 22.09, п.2, страховка лимита 10с приватной проверки, reports/
devops-hack-v13.md): `warm_up_label_verifier()` дополнительно греет RapidOCR/
PaddleOCR во ВСЕХ масштабах (включая третий проход по кропу этикетки, который
плейсхолдер 2x2 не может реально прогреть — нет ни одного бокса текста) и
делает один запрос к VLM-шлюзу, если адрес задан.

Юниты — фейковые `LabelVerifier`/`vision_llm.read_label`, без сети и тяжёлых
моделей (ml-engineer.md: "без тяжёлых моделей в юнитах"). Один ГЕЙТИРОВАННЫЙ
smoke-тест на настоящем `cv.ocr_rapid.RapidOcrReader` — тот же паттерн допуска,
что tests/test_integration_real_cv.py (RUN_CV_INTEGRATION=1 + пакет
установлен), эмпирически подтверждает, что синтетическая картинка реально
детектируется движком (не только фейком в юнитах выше).
"""
from __future__ import annotations

import dataclasses
import io
import os

import pytest

from app.config import Settings
from app.cv import factory

try:
    import cv.ocr_rapid  # noqa: F401 — наличие проверяем самим импортом
    _RAPIDOCR_IMPORTABLE = True
except ImportError:
    _RAPIDOCR_IMPORTABLE = False

_RUN_FLAG = os.environ.get("RUN_CV_INTEGRATION") == "1"


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


# --------------------------------------------------------------------------------------
# _synthetic_label_image() — генерация, без сети/тяжёлых моделей
# --------------------------------------------------------------------------------------


def test_synthetic_label_image_is_a_decodable_jpeg():
    data = factory._synthetic_label_image()
    assert data[:2] == b"\xff\xd8", "JPEG-сигнатура"
    assert len(data) > 5000, "не крошечный плейсхолдер — настоящая картинка с текстом"

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im.load()
        w, h = im.size
    assert w >= 500 and h >= 500


def test_synthetic_label_image_is_deterministic_shape_each_call():
    """Не кэшируется на уровне модуля (генерируется по требованию) — но форма
    (размер/формат) стабильна между вызовами."""
    a = factory._synthetic_label_image()
    b = factory._synthetic_label_image()
    assert a[:2] == b[:2] == b"\xff\xd8"
    assert abs(len(a) - len(b)) < 2000  # JPEG-кодирование детерминировано на том же входе


def test_label_font_never_raises_regardless_of_system_fonts():
    """`_label_font()` обязан деградировать до `ImageFont.load_default()`, не
    падать, даже если ни один кандидат из _CYRILLIC_FONT_CANDIDATES не найден
    (стенд ams3 не ставит шрифты вовсе, infra/ams3/bootstrap.sh)."""
    font = factory._label_font(32)
    assert font is not None


# --------------------------------------------------------------------------------------
# warm_up_label_verifier(): CV_FUSION=1 греет текстовую ветку поверх плейсхолдера
# --------------------------------------------------------------------------------------


class _RapidCountingVerifier:
    """`ocr_engine="rapid"` — та же ветка прогрева, что H2 (read_query_text(),
    не verify()); запоминает КАЖДОЕ переданное изображение, чтобы отличить
    плейсхолдер (базовый прогрев) от синтетической этикетки (CV_FUSION)."""

    ocr_engine = "rapid"

    def __init__(self) -> None:
        self.images: list[bytes] = []

    def read_query_text(self, image: bytes) -> str:
        self.images.append(image)
        return ""

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise AssertionError("rapid-движок: verify() не должен вызываться на прогреве")


def test_warm_up_calls_fusion_branch_in_addition_to_baseline_when_cv_fusion_enabled():
    verifier = _RapidCountingVerifier()
    settings = _settings(verifier_provider="real", cv_fusion=True)
    assert factory.warm_up_label_verifier(verifier, settings) is True
    assert len(verifier.images) == 2, "плейсхолдер (базовый прогрев) + синтетическая этикетка (CV_FUSION)"
    assert verifier.images[0] == factory._PLACEHOLDER_IMAGE
    assert verifier.images[1] != factory._PLACEHOLDER_IMAGE
    assert len(verifier.images[1]) > 1000, "вторая картинка — не крошечный плейсхолдер"


def test_warm_up_skips_fusion_branch_when_cv_fusion_disabled():
    verifier = _RapidCountingVerifier()
    settings = _settings(verifier_provider="real", cv_fusion=False)
    assert factory.warm_up_label_verifier(verifier, settings) is True
    assert len(verifier.images) == 1, "CV_FUSION=0 — только базовый прогрев, как раньше"


def test_warm_up_mock_provider_skips_everything_even_with_cv_fusion_on():
    class _MustNotBeCalled:
        ocr_engine = "rapid"

        def read_query_text(self, image: bytes) -> str:
            raise AssertionError("mock-провайдер: read_query_text() не должен вызываться")

        def verify(self, image: bytes, candidates: list[dict]) -> str | None:
            raise AssertionError("mock-провайдер: verify() не должен вызываться")

    settings = _settings(verifier_provider="mock", cv_fusion=True)
    assert factory.warm_up_label_verifier(_MustNotBeCalled(), settings) is True


class _RapidFailsOnFusionImage:
    """Плейсхолдер (первый вызов) проходит — синтетическая этикетка (второй)
    роняет движок, симулируя реальный сбой ИМЕННО на прогреве текстовой ветки."""

    ocr_engine = "rapid"

    def __init__(self) -> None:
        self.calls = 0

    def read_query_text(self, image: bytes) -> str:
        self.calls += 1
        if self.calls == 1:
            return ""
        raise RuntimeError("движок RapidOCR упал на синтетической этикетке")

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise AssertionError


def test_warm_up_fusion_branch_ocr_failure_marks_unwarm_without_raising():
    verifier = _RapidFailsOnFusionImage()
    settings = _settings(verifier_provider="real", cv_fusion=True)
    assert factory.warm_up_label_verifier(verifier, settings) is False
    assert verifier.calls == 2


# --------------------------------------------------------------------------------------
# VLM-шлюз: один запрос при CV_FUSION=1 и заданном VISION_LLM_URL; сбой шлюза
# не должен ронять старт и не должен делать warm=false (прямое указание брифа)
# --------------------------------------------------------------------------------------


def test_warm_up_calls_vlm_gateway_once_when_url_is_set(monkeypatch):
    calls: list[dict] = []

    def fake_read_label(image_bytes: bytes, **kw) -> str:
        calls.append(kw)
        return "текст с синтетической этикетки"

    monkeypatch.setattr(factory.vision_llm, "read_label", fake_read_label)
    verifier = _RapidCountingVerifier()
    settings = _settings(
        verifier_provider="real", cv_fusion=True,
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="secret-key",
    )
    assert factory.warm_up_label_verifier(verifier, settings) is True
    assert len(calls) == 1
    assert calls[0]["url"] == "http://fake-gateway.invalid"
    assert calls[0]["key"] == "secret-key"


def test_warm_up_skips_vlm_call_when_url_not_set(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(factory.vision_llm, "read_label", lambda *a, **kw: calls.append(kw) or "")
    verifier = _RapidCountingVerifier()
    settings = _settings(verifier_provider="real", cv_fusion=True, vision_llm_url=None)
    assert factory.warm_up_label_verifier(verifier, settings) is True
    assert calls == []


def test_warm_up_vlm_gateway_failure_does_not_raise_and_does_not_mark_unwarm(monkeypatch, caplog):
    """Брифа п.2: "сбой прогрева VLM не должен ронять старт и не должен делать
    warm=false — только лог"."""
    import logging

    def raising(image_bytes: bytes, **kw) -> str:
        raise RuntimeError("шлюз недоступен на старте")

    monkeypatch.setattr(factory.vision_llm, "read_label", raising)
    verifier = _RapidCountingVerifier()  # OCR-часть успешна
    settings = _settings(
        verifier_provider="real", cv_fusion=True,
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="secret-key",
    )
    with caplog.at_level(logging.WARNING, logger="app.cv.factory"):
        result = factory.warm_up_label_verifier(verifier, settings)
    assert result is True, "сбой VLM НЕ должен переводить прогрев в warm=false"
    messages = [r.getMessage() for r in caplog.records]
    assert any("VLM" in m for m in messages)
    assert not any("secret-key" in m for m in messages), "ключ шлюза не логируется"


def test_warm_up_logs_fusion_branch_timing(caplog):
    import logging

    verifier = _RapidCountingVerifier()
    settings = _settings(verifier_provider="real", cv_fusion=True)
    with caplog.at_level(logging.INFO, logger="app.cv.factory"):
        factory.warm_up_label_verifier(verifier, settings)
    messages = [r.getMessage() for r in caplog.records]
    assert any("прогрет" in m for m in messages), "время прогрева должно попасть в лог (брифа п.2)"


# --------------------------------------------------------------------------------------
# Smoke на РЕАЛЬНОМ RapidOCR (гейтировано, тот же паттерн, что
# test_integration_real_cv.py) — подтверждает, что синтетическая картинка
# реально детектируется движком и третий проход (кроп этикетки) не остаётся
# формальностью.
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_RUN_FLAG and _RAPIDOCR_IMPORTABLE),
    reason="RUN_CV_INTEGRATION=1 и rapidocr не заданы/не установлены — см. test_integration_real_cv.py",
)
def test_synthetic_label_image_triggers_real_rapidocr_label_crop_pass():
    import numpy as np
    from PIL import Image

    from cv.label_crop import label_bbox
    from cv.ocr_rapid import RapidOcrReader, _resize_to_longest_side
    from cv.verify import _center_crop

    data = factory._synthetic_label_image()
    arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    cropped = _center_crop(arr)

    reader = RapidOcrReader(sizes=(640, 960))
    text = reader.read_center(cropped)
    assert text.strip(), "движок обязан прочитать хоть что-то с синтетической этикетки"

    # Боксы масштаба 960 (max(sizes)) реально ведут к валидному label_bbox() —
    # иначе третий проход (CV_OCR_LABEL_SIZE) конструирует движок, но никогда
    # не вызывает _run_engine() на нём (см. докстринг _read_label_pass).
    engine_960 = reader._engine_for(960)
    scaled = np.ascontiguousarray(_resize_to_longest_side(cropped, 960))
    result = reader._run_engine(engine_960, 960, scaled)
    assert result is not None and result.boxes is not None and len(result.boxes) > 0
    h, w = scaled.shape[:2]
    assert label_bbox(result.boxes, result.scores or (), w, h) is not None
