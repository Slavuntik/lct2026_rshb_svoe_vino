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
from app.cv.factory import (
    get_image_index,
    get_label_verifier,
    warm_up_image_index,
    warm_up_label_verifier,
)
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


class _SearchOK:
    def search(self, image: bytes, top_k: int = 5) -> list:
        return []


class _SearchExplodes:
    def search(self, image: bytes, top_k: int = 5) -> list:
        raise RuntimeError("store/энкодер недоступны — ровно то, что warm-up обязан пережить")


class _SearchMustNotBeCalled:
    def search(self, image: bytes, top_k: int = 5) -> list:
        raise AssertionError("mock-провайдер не должен вызывать search() при прогреве вообще")


def test_warm_up_skips_search_entirely_on_mock_provider():
    """IMAGE_PROVIDER=mock — нечего греть, True без обращения к search()."""
    assert warm_up_image_index(_SearchMustNotBeCalled(), _settings(image_provider="mock")) is True


def test_warm_up_returns_true_when_real_index_searches_successfully():
    """v0.4.7 (TODO-1 ревью 05): прогрев зовёт ИМЕННО search(), не embed() —
    измерено напрямую (reports/b4-gate-v047.md), что embed() прогревает
    только энкодер, а ~2 с холодного старта на этом каталоге (49650 точек)
    сидят в первом обращении embedded Qdrant-стора внутри search(), которое
    embed() вообще не задевает."""
    assert warm_up_image_index(_SearchOK(), _settings(image_provider="real")) is True


def test_warm_up_returns_false_without_raising_when_real_index_search_fails():
    """Ошибка прогрева не должна ронять вызывающий код (app/main.py::create_app)
    — лучше поднятый процесс с warm=False, чем не поднятый вовсе."""
    assert warm_up_image_index(_SearchExplodes(), _settings(image_provider="real")) is False


# --- v0.4.7 (контракт §5, TODO-1 ревью 05): прогрев LabelVerifier -----------

class _VerifyOK:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        self.calls.append(candidates)
        return None


class _VerifyExplodes:
    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise RuntimeError("OCR-движок недоступен — ровно то, что warm-up обязан пережить")


class _VerifyMustNotBeCalled:
    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise AssertionError("mock-провайдер не должен вызывать verify() при прогреве вообще")


def test_warm_up_verifier_skips_verify_entirely_on_mock_provider():
    """VERIFIER_PROVIDER=mock — нечего греть, True без обращения к verify()."""
    assert warm_up_label_verifier(_VerifyMustNotBeCalled(), _settings(verifier_provider="mock")) is True


def test_warm_up_verifier_returns_true_and_calls_verify_with_nonempty_candidates():
    """packages/cv/cv/verify.py::LabelVerifier.verify() возвращает None РАНЬШЕ
    _load()/read_text() на пустом списке кандидатов (`if not candidates:
    return None`) — прогрев с candidates=[] был бы пустышкой, PaddleOCR так и
    остался бы не загружен. warm_up_label_verifier() обязан передать ХОТЯ БЫ
    одного (фиктивного) кандидата, иначе холодный старт всё равно ловит
    первый боевой near-dup запрос (репетиция B3, +2 с, c03edd0)."""
    verifier = _VerifyOK()
    assert warm_up_label_verifier(verifier, _settings(verifier_provider="real")) is True
    assert len(verifier.calls) == 1
    assert len(verifier.calls[0]) >= 1, "прогрев обязан передать непустой список кандидатов"


def test_warm_up_verifier_returns_false_without_raising_when_real_verifier_fails():
    """Симметрично warm_up_image_index — ошибка прогрева не роняет старт."""
    assert warm_up_label_verifier(_VerifyExplodes(), _settings(verifier_provider="real")) is False


# --- agents/H2-rapidocr-multiscale.md: warm_up_label_verifier() греет ВЫБРАННЫЙ
# движок (verifier.ocr_engine) — "rapid" греет read_query_text(), не verify() -----


class _RapidVerifyMustNotBeCalled:
    """`verify()` НЕ должен вызываться на прогреве в режиме rapid — та на своём
    внутреннем fallback-пути (без ocr_text) ВСЕГДА грузит PaddleOCR (packages/cv/
    cv/verify.py), а весь смысл CV_OCR_ENGINE=rapid — не тащить PaddleOCR в
    память на дешёвом CPU."""

    ocr_engine = "rapid"

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise AssertionError("rapid-движок: verify() не должен вызываться на прогреве")

    def read_query_text(self, image: bytes) -> str:
        return ""


def test_warm_up_verifier_rapid_engine_does_not_call_verify():
    assert warm_up_label_verifier(_RapidVerifyMustNotBeCalled(), _settings(verifier_provider="real")) is True


class _RapidReadQueryTextOK:
    ocr_engine = "rapid"

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def read_query_text(self, image: bytes) -> str:
        self.calls.append(image)
        return ""

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        raise AssertionError("rapid-движок: verify() не должен вызываться на прогреве")


def test_warm_up_verifier_rapid_engine_calls_read_query_text_exactly_once():
    verifier = _RapidReadQueryTextOK()
    assert warm_up_label_verifier(verifier, _settings(verifier_provider="real")) is True
    assert len(verifier.calls) == 1


class _RapidReadQueryTextExplodes:
    ocr_engine = "rapid"

    def read_query_text(self, image: bytes) -> str:
        raise RuntimeError("движок RapidOCR недоступен — ровно то, что warm-up обязан пережить")


def test_warm_up_verifier_rapid_engine_returns_false_without_raising_on_failure():
    assert warm_up_label_verifier(_RapidReadQueryTextExplodes(), _settings(verifier_provider="real")) is False


def test_warm_up_verifier_rapid_engine_skips_entirely_on_mock_provider():
    """VERIFIER_PROVIDER=mock — нечего греть независимо от ocr_engine, ни verify(),
    ни read_query_text() не должны вызываться."""

    class _RapidMustNotBeCalledAtAll:
        ocr_engine = "rapid"

        def verify(self, image, candidates):
            raise AssertionError("mock-провайдер: verify() не должен вызываться")

        def read_query_text(self, image):
            raise AssertionError("mock-провайдер: read_query_text() не должен вызываться")

    assert warm_up_label_verifier(_RapidMustNotBeCalledAtAll(), _settings(verifier_provider="mock")) is True


class _PaddleEngineExplicitStillUsesVerify:
    """Регресс: `ocr_engine == "paddle"` (явно, не только отсутствие атрибута)
    обязан сохранить СТАРОЕ поведение (verify()), даже когда объект технически
    несёт read_query_text — дефолт брифа 'paddle до приёмки' не переключается
    молча."""

    ocr_engine = "paddle"

    def __init__(self) -> None:
        self.verify_calls = 0

    def verify(self, image: bytes, candidates: list[dict]) -> str | None:
        self.verify_calls += 1
        return None

    def read_query_text(self, image: bytes) -> str:
        raise AssertionError("paddle-движок не должен вызывать read_query_text() на прогреве")


def test_warm_up_verifier_explicit_paddle_engine_still_uses_verify_not_read_query_text():
    verifier = _PaddleEngineExplicitStillUsesVerify()
    assert warm_up_label_verifier(verifier, _settings(verifier_provider="real")) is True
    assert verifier.verify_calls == 1


def test_warm_up_verifier_object_without_ocr_engine_attribute_still_uses_verify():
    """Регресс на СУЩЕСТВУЮЩИЕ двойники этого файла (`_VerifyOK` и т.п.) — они не
    несут `ocr_engine` вовсе; `getattr(..., "paddle")` обязан мягко деградировать
    к старому поведению, не падать с AttributeError."""
    verifier = _VerifyOK()
    assert warm_up_label_verifier(verifier, _settings(verifier_provider="real")) is True
    assert len(verifier.calls) == 1
