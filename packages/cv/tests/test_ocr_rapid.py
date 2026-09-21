"""Тесты `cv/ocr_rapid.py` (agents/H2-rapidocr-multiscale.md) — RapidOCR в несколько
масштабов. БЕЗ реального rapidocr/onnxruntime движка (брифа п.5: "юнит-тесты не должны
требовать rapidocr/onnxruntime") — та же дисциплина, что `test_verify.py` уже применяет
к PaddleOCR (`_StubOCR`): либо готовый движок подменяется НАПРЯМУЮ в `reader._engines`
(минуя `_engine_for()`/реальный импорт целиком), либо сам модуль `rapidocr` подменяется в
`sys.modules` (проверка параметров конструктора/деградации на импорте/конструкторе, без
скачивания настоящих моделей/сети).
"""
from __future__ import annotations

import sys
import types
import warnings
from dataclasses import dataclass

import numpy as np
import pytest

from cv.ocr_rapid import (
    DEFAULT_RAPID_SIZES,
    DEFAULT_SCORE_THRESH,
    RapidOcrReader,
    _downscale_to_longest_side,
    parse_sizes,
)
from cv.verify import CENTER_CROP

# --------------------------------------------------------------------------------------
# parse_sizes()
# --------------------------------------------------------------------------------------


def test_parse_sizes_comma_separated():
    assert parse_sizes("640,960") == (640, 960)


def test_parse_sizes_tolerates_surrounding_whitespace():
    assert parse_sizes(" 640 , 960 ") == (640, 960)


def test_parse_sizes_single_value():
    assert parse_sizes("640") == (640,)


def test_parse_sizes_empty_string_raises_value_error():
    with pytest.raises(ValueError):
        parse_sizes("")


def test_parse_sizes_only_commas_raises_value_error():
    with pytest.raises(ValueError):
        parse_sizes(",,")


def test_parse_sizes_non_numeric_raises():
    with pytest.raises(ValueError):
        parse_sizes("640,abc")


# --------------------------------------------------------------------------------------
# _downscale_to_longest_side() — см. докстринг модуля, "Явный даунскейл ДО вызова движка"
# --------------------------------------------------------------------------------------


def test_downscale_to_longest_side_no_op_when_already_within_target():
    arr = np.zeros((100, 200, 3), dtype=np.uint8)
    assert _downscale_to_longest_side(arr, 640) is arr  # no-op — тот же объект, без копии


def test_downscale_to_longest_side_no_op_at_exact_boundary():
    arr = np.zeros((640, 400, 3), dtype=np.uint8)
    assert _downscale_to_longest_side(arr, 640) is arr  # ровно на границе — <=, не <


def test_downscale_to_longest_side_shrinks_preserving_aspect_ratio():
    arr = np.zeros((930, 1400, 3), dtype=np.uint8)
    assert _downscale_to_longest_side(arr, 640).shape == (425, 640, 3)


def test_downscale_to_longest_side_never_upscales_smaller_input():
    arr = np.zeros((50, 80, 3), dtype=np.uint8)
    result = _downscale_to_longest_side(arr, 640)
    assert result.shape == (50, 80, 3)


def test_downscale_to_longest_side_matches_pil_thumbnail_pixel_for_pixel():
    """Регресс-защита находки приёмки живым API (agents/H2-rapidocr-multiscale.md,
    задача 7): ДОЛЖЕН быть `PIL.Image.thumbnail()` (BICUBIC), НЕ `cv2.resize(...,
    INTER_AREA)` — сравнено эмпирически на реальных фото, разные фильтры
    интерполяции дают расхождение пикселей 18-27/255 и теряют/восстанавливают
    целые распознанные слова на границе разрешимости мелкого текста (см. докстринг
    модуля, "Явный даунскейл ДО вызова движка"). Не текстовый градиент (случайные
    пиксели, не однотонная заливка) — иначе оба фильтра тривиально совпали бы."""
    from PIL import Image

    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, size=(930, 1400, 3), dtype=np.uint8)

    result = _downscale_to_longest_side(arr, 640)

    expected_im = Image.fromarray(arr, mode="RGB")
    expected_im.thumbnail((640, 640))
    expected = np.asarray(expected_im)
    assert np.array_equal(result, expected)


# --------------------------------------------------------------------------------------
# RapidOcrReader — конструктор/дефолты (ленивая загрузка: не трогает rapidocr вовсе)
# --------------------------------------------------------------------------------------


def test_default_rapid_sizes_is_640_960():
    assert DEFAULT_RAPID_SIZES == (640, 960)
    assert RapidOcrReader().sizes == (640, 960)


def test_default_score_thresh_is_half():
    assert DEFAULT_SCORE_THRESH == 0.5
    assert RapidOcrReader().score_thresh == 0.5


def test_custom_sizes_and_thresh_respected():
    r = RapidOcrReader(sizes=(640,), score_thresh=0.7)
    assert r.sizes == (640,)
    assert r.score_thresh == 0.7


def test_construction_does_not_import_rapidocr(monkeypatch):
    """Конструктор — дешёвый (как `cv.verify.LabelVerifier`/`cv.encoder.SiglipEncoder`):
    не трогает rapidocr вовсе, пока `read()`/`read_center()` реально не понадобился."""
    monkeypatch.setitem(sys.modules, "rapidocr", None)  # `import rapidocr` -> ImportError гарантированно
    RapidOcrReader()  # не должен поднять исключение


# --------------------------------------------------------------------------------------
# read()/read_center() — с ПОДМЕНЁННЫМ движком (напрямую в _engines, минуя _engine_for())
# --------------------------------------------------------------------------------------


@dataclass
class _StubRapidResult:
    txts: tuple[str, ...] | None
    scores: tuple[float, ...] | None


class _StubRapidEngine:
    """Двойник ОДНОГО движка RapidOCR (см. `rapidocr.utils.output.RapidOCROutput` —
    `.txts`/`.scores`) — тот же приём, что `_StubOCR` в test_verify.py: подменяет
    ГОТОВЫЙ движок напрямую в `reader._engines[size]`, реальный импорт/сеть не трогая."""

    def __init__(self, result: _StubRapidResult | None = None, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[np.ndarray] = []

    def __call__(self, image_arr: np.ndarray):
        self.calls.append(image_arr)
        if self.raises is not None:
            raise self.raises
        return self.result


def _reader_with_stub_engines(engines: dict[int, _StubRapidEngine], **kwargs) -> RapidOcrReader:
    reader = RapidOcrReader(sizes=tuple(engines), **kwargs)
    reader._engines.update(engines)
    return reader


def test_read_joins_texts_from_all_configured_scales_with_space():
    e640 = _StubRapidEngine(_StubRapidResult(txts=("АБВ",), scores=(0.9,)))
    e960 = _StubRapidEngine(_StubRapidResult(txts=("ГДЕ",), scores=(0.9,)))
    reader = _reader_with_stub_engines({640: e640, 960: e960})

    text = reader.read(np.zeros((100, 100, 3), dtype=np.uint8))

    assert text == "АБВ ГДЕ"


def test_read_filters_texts_below_score_threshold():
    engine = _StubRapidEngine(_StubRapidResult(txts=("low", "high"), scores=(0.3, 0.9)))
    reader = _reader_with_stub_engines({640: engine})

    assert reader.read(np.zeros((10, 10, 3), dtype=np.uint8)) == "high"


def test_read_score_threshold_boundary_is_inclusive():
    """DEFAULT_SCORE_THRESH=0.5, ровно на границе — засчитывается (>=, не >), та же
    дисциплина, что `LabelVerifier.read_text()` (packages/cv/cv/verify.py)."""
    engine = _StubRapidEngine(_StubRapidResult(txts=("ровно",), scores=(0.5,)))
    reader = _reader_with_stub_engines({640: engine})

    assert reader.read(np.zeros((10, 10, 3), dtype=np.uint8)) == "ровно"


def test_read_multiple_texts_within_one_scale_joined_by_space():
    engine = _StubRapidEngine(_StubRapidResult(txts=("МУСКАТЕЛЬ", "БЕЛЫЙ"), scores=(0.9, 0.8)))
    reader = _reader_with_stub_engines({640: engine})

    assert reader.read(np.zeros((10, 10, 3), dtype=np.uint8)) == "МУСКАТЕЛЬ БЕЛЫЙ"


def test_read_engine_result_none_degrades_to_empty_string():
    engine = _StubRapidEngine(result=None)
    reader = _reader_with_stub_engines({640: engine})

    assert reader.read(np.zeros((10, 10, 3), dtype=np.uint8)) == ""


def test_read_engine_result_empty_txts_degrades_to_empty_string():
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})

    assert reader.read(np.zeros((10, 10, 3), dtype=np.uint8)) == ""


def test_read_one_scale_call_failure_does_not_prevent_other_scale():
    """Сбой движка НА КОНКРЕТНОМ вызове (не на импорте/конструкторе) — тоже
    деградация, не исключение: остальные масштабы отрабатывают как обычно."""
    broken = _StubRapidEngine(raises=RuntimeError("onnxruntime упал на этом кадре"))
    ok = _StubRapidEngine(_StubRapidResult(txts=("РУБИН",), scores=(0.9,)))
    reader = _reader_with_stub_engines({640: broken, 960: ok})

    with pytest.warns(UserWarning, match="640"):
        text = reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert text == "РУБИН"


def test_read_all_scales_fail_returns_empty_string_not_exception():
    broken1 = _StubRapidEngine(raises=RuntimeError("движок 1 упал"))
    broken2 = _StubRapidEngine(raises=RuntimeError("движок 2 упал"))
    reader = _reader_with_stub_engines({640: broken1, 960: broken2})

    with pytest.warns(UserWarning):
        text = reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert text == ""


def test_read_calls_each_scale_engine_exactly_once_per_read():
    e640 = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    e960 = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: e640, 960: e960})

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert len(e640.calls) == 1
    assert len(e960.calls) == 1


def test_read_passes_contiguous_array_to_engine():
    """`np.ascontiguousarray` — тот же довод, что `cv.verify._center_crop`
    (докстринг там): срез numpy может не владеть памятью непрерывно построчно, а
    движок ожидает C-contiguous вход."""
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})
    non_contiguous = np.zeros((20, 20, 3), dtype=np.uint8)[::2, ::2]  # срез -> не C-contiguous
    assert not non_contiguous.flags["C_CONTIGUOUS"]

    reader.read(non_contiguous)

    assert engine.calls[0].flags["C_CONTIGUOUS"]


# --------------------------------------------------------------------------------------
# read_center() — тот же центральный кроп, что cv.verify (CENTER_CROP), НЕ детектор
# --------------------------------------------------------------------------------------


def test_read_center_crops_full_frame_by_center_crop_fractions_when_no_downscale_needed():
    """Небольшой кадр (кроп уже <= target) — даунскейл не срабатывает, доли
    `CENTER_CROP` проверяются побитово, без искажения ресайзом."""
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})
    full_frame = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)

    reader.read_center(full_frame)

    assert engine.calls[0].shape == (93, 140, 3)  # (0.98-0.05)*100, (0.85-0.15)*200 — см. CENTER_CROP
    assert np.array_equal(engine.calls[0], full_frame[5:98, 30:170])


def test_read_center_downscales_large_crop_to_target_size_before_engine():
    """Кроп КРУПНЕЕ `size` (типичный телефонный кадр) — обязан дойти до движка
    ДАУНСКЕЙЛЕННЫМ до `size` по длинной стороне (docstring модуля, "Явный
    даунскейл ДО вызова движка"), не в исходном разрешении кропа. Те же числа,
    что H1 намерил для ТОГО ЖЕ входа через PaddleOCR-путь (packages/cv/tests/
    test_verify.py::test_read_query_text_center_feeds_full_frame_crop_not_detector_square
    — 930x1400 -> 425x640): RapidOCR-путь обязан вести себя идентично на этом шаге."""
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})
    full_frame = np.zeros((1000, 2000, 3), dtype=np.uint8)  # доли CENTER_CROP -> кроп 930x1400 (> 640)

    reader.read_center(full_frame)

    assert engine.calls[0].shape == (425, 640, 3)  # 930x1400 -> downscale до максимума 640 (PIL.Image.thumbnail)
    assert max(engine.calls[0].shape[:2]) == 640


def test_read_downscales_each_scale_independently_to_its_own_target():
    """Два масштаба на ОДНОМ И ТОМ ЖЕ большом кропе — каждый движок обязан увидеть
    СВОЙ даунскейл (640 и 960 — разное разрешение, не общий для обоих масштабов),
    иначе двухмасштабный трюк вырождается в "один и тот же вход дважды" (см.
    докстринг модуля — эмпирическая находка на живом фото)."""
    e640 = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    e960 = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: e640, 960: e960})
    big = np.zeros((1000, 2000, 3), dtype=np.uint8)

    reader.read(big)

    assert max(e640.calls[0].shape[:2]) == 640
    assert max(e960.calls[0].shape[:2]) == 960
    assert e640.calls[0].shape != e960.calls[0].shape


def test_center_crop_fractions_match_cv_verify_constant():
    """RapidOCR-путь читает ТЕ ЖЕ доли, что H1 (agents/H1-cpu-path.md) — не
    отдельно подобранные числа (регресс-защита от рассинхрона двух модулей):
    `read_center()` делегирует `cv.verify._center_crop()` напрямую, эта константа —
    единственный источник правды для обоих."""
    assert CENTER_CROP == (0.15, 0.05, 0.85, 0.98)


# --------------------------------------------------------------------------------------
# _engine_for() — деградация на импорте/конструкторе; реальный `rapidocr` подменяется
# в sys.modules (не устанавливаем и не скачиваем настоящие модели/веса)
# --------------------------------------------------------------------------------------


def _install_fake_rapidocr_module(monkeypatch, *, construct=None):
    """Минимальная подмена пакета `rapidocr` — модуль с `RapidOCR`/`LangRec`/
    `OCRVersion`/`ModelType`, как их видит `from rapidocr import ...` внутри
    `_engine_for()`. `construct` — callable(params=...) -> движок (или исключение)."""
    fake = types.SimpleNamespace(
        RapidOCR=construct or (lambda params=None: _StubRapidEngine(_StubRapidResult((), ()))),
        LangRec=types.SimpleNamespace(ESLAV="eslav-marker"),
        OCRVersion=types.SimpleNamespace(PPOCRV5="ppocrv5-marker"),
        ModelType=types.SimpleNamespace(MOBILE="mobile-marker", SERVER="server-marker"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake)
    return fake


def test_engine_for_degrades_gracefully_when_rapidocr_not_importable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    reader = RapidOcrReader(sizes=(640,))

    with pytest.warns(UserWarning, match="rapidocr"):
        text = reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert text == ""


def test_engine_for_degrades_gracefully_when_engine_construction_fails(monkeypatch):
    def _boom(params=None):
        raise RuntimeError("сеть недоступна — модель RapidOCR не скачать")

    _install_fake_rapidocr_module(monkeypatch, construct=_boom)
    reader = RapidOcrReader(sizes=(640,))

    with pytest.warns(UserWarning, match="640"):
        text = reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert text == ""


def test_engine_for_unavailable_size_does_not_retry_construction_on_next_read(monkeypatch):
    """Сбой конструктора ЗАПОМИНАЕТСЯ на масштаб (`_unavailable_sizes`) — второй
    `read()` не пытается пересоздать движок заново (не плодит новый warning на
    каждый запрос при постоянно недоступной сети/пакете)."""
    attempts = {"n": 0}

    def _boom(params=None):
        attempts["n"] += 1
        raise RuntimeError("недоступно")

    _install_fake_rapidocr_module(monkeypatch, construct=_boom)
    reader = RapidOcrReader(sizes=(640,))

    with pytest.warns(UserWarning):
        reader.read(np.zeros((10, 10, 3), dtype=np.uint8))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        reader.read(np.zeros((10, 10, 3), dtype=np.uint8))
        assert len(w) == 0, "второй read() не должен снова предупреждать/пытаться конструировать"

    assert attempts["n"] == 1


def test_engine_for_passes_prototype_params_verbatim(monkeypatch):
    """agents/H2-rapidocr-multiscale.md: "параметры движка — оттуда, дословно"
    (qa/real_photos_rapidocr.py) — регресс-защита буквальных значений конфигурации."""
    captured: list[dict] = []

    def _capture(params=None):
        captured.append(dict(params or {}))
        return _StubRapidEngine(_StubRapidResult((), ()))

    _install_fake_rapidocr_module(monkeypatch, construct=_capture)
    reader = RapidOcrReader(sizes=(640,))

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert captured == [{
        "Global.use_cls": False,
        "Det.limit_side_len": 640,
        "Det.limit_type": "max",
        "Det.ocr_version": "ppocrv5-marker",
        "Det.model_type": "mobile-marker",
        "Rec.lang_type": "eslav-marker",
        "Rec.ocr_version": "ppocrv5-marker",
        "Rec.model_type": "mobile-marker",
    }]


def test_engine_for_creates_separate_engine_per_scale_with_correct_limit_side_len(monkeypatch):
    """Брифа п.1: "один экземпляр движка на масштаб" — `Det.limit_side_len` не
    входит в параметры вызова RapidOCR (проверено против реального API, см.
    докстринг модуля cv/ocr_rapid.py), только конструктора: каждый масштаб обязан
    получить СВОЙ движок с правильным значением, не один переиспользованный."""
    captured: dict[int, dict] = {}

    def _capture(params=None):
        captured[params["Det.limit_side_len"]] = dict(params)
        return _StubRapidEngine(_StubRapidResult((), ()))

    _install_fake_rapidocr_module(monkeypatch, construct=_capture)
    reader = RapidOcrReader(sizes=(640, 960))

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert set(captured) == {640, 960}
    assert captured[640]["Det.limit_side_len"] == 640
    assert captured[960]["Det.limit_side_len"] == 960


def test_engine_for_caches_engine_instance_across_multiple_reads(monkeypatch):
    construct_count = {"n": 0}

    def _factory(params=None):
        construct_count["n"] += 1
        return _StubRapidEngine(_StubRapidResult((), ()))

    _install_fake_rapidocr_module(monkeypatch, construct=_factory)
    reader = RapidOcrReader(sizes=(640, 960))

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))
    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert construct_count["n"] == 2  # по одному конструктору НА МАСШТАБ за всё время, не на read()
