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
from typing import Any

import numpy as np
import pytest

from cv.ocr_rapid import (
    DEFAULT_LABEL_SIZE,
    DEFAULT_RAPID_SIZES,
    DEFAULT_SCORE_THRESH,
    RapidOcrReader,
    _downscale_to_longest_side,
    _resize_to_longest_side,
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
    # agents/H3-label-crop-ocr.md: боксы масштаба-источника переиспользуются третьим
    # проходом (см. RapidOcrReader._read_label_pass()) — дефолт None, чтобы СУЩЕСТВУЮЩИЕ
    # вызовы _StubRapidResult(txts=..., scores=...) (без boxes) не ломались; тесты
    # третьего прохода передают его явно.
    boxes: Any | None = None


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
    """agents/H3-label-crop-ocr.md: `label_size=0` ПО УМОЛЧАНИЮ здесь — третий проход
    выключен для ВСЕХ тестов этого хелпера, если явно не запрошен `label_size=...`
    (см. раздел "label-пасс" ниже) — тесты выше/большинство ниже проверяют механику
    `self.sizes` (640/960) и не должны попутно конструировать/греть ЕЩЁ один движок
    (дефолт `RapidOcrReader` — 1280) или трогать `result.boxes` стабов, которые его не
    несут."""
    kwargs.setdefault("label_size", 0)
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


def test_read_center_crop_exactly_at_target_is_unchanged():
    """Кроп РОВНО на границе `target` по длинной стороне (640) — ни уменьшение, ни
    увеличение не срабатывают (agents/H3-label-crop-ocr.md, задача 3 — граница `>=`
    уходит в `_downscale_to_longest_side()`, а та на границе — no-op): доли
    `CENTER_CROP` проверяются побитово, без искажения ресайзом. W=915 подобрано так,
    чтобы `int(915*0.85) - int(915*0.15) == 640` (усечение `_center_crop()`)."""
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})
    full_frame = np.arange(100 * 915 * 3, dtype=np.uint8).reshape(100, 915, 3)

    reader.read_center(full_frame)

    assert engine.calls[0].shape == (93, 640, 3)  # (0.98-0.05)*100, ровно 640 по x — см. CENTER_CROP
    assert np.array_equal(engine.calls[0], full_frame[5:98, 137:777])


def test_read_center_upscales_small_crop_via_bicubic_before_engine():
    """Небольшой кадр (кроп 93x140, СТРОГО МЕНЬШЕ target=640 по обеим сторонам) —
    agents/H3-label-crop-ocr.md, задача 3: до этой волны такой кроп доходил до
    движка БЕЗ изменений (H2, `_downscale_to_longest_side()` — "только уменьшает");
    теперь `_resize_to_longest_side()` увеличивает его бикубически до 640 по длинной
    стороне — движок видит БОЛЬШЕ пикселей на ту же строку текста, не исходные 93x140."""
    engine = _StubRapidEngine(_StubRapidResult(txts=(), scores=()))
    reader = _reader_with_stub_engines({640: engine})
    full_frame = np.arange(100 * 200 * 3, dtype=np.uint8).reshape(100, 200, 3)

    reader.read_center(full_frame)

    # 93x140 (доли CENTER_CROP от 100x200), увеличено до max=640 по длинной стороне (140)
    assert engine.calls[0].shape == (425, 640, 3)
    assert max(engine.calls[0].shape[:2]) == 640
    # НЕ исходный кроп — реально увеличенный (иначе тест ничего не проверял бы)
    assert engine.calls[0].shape != (93, 140, 3)


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
    # label_size=0 — эта секция про _engine_for()/self.sizes (640/960), третий проход
    # (дефолт 1280) не должен попутно плодить свой собственный импорт/warning здесь.
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    reader = RapidOcrReader(sizes=(640,), label_size=0)

    with pytest.warns(UserWarning, match="rapidocr"):
        text = reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert text == ""


def test_engine_for_degrades_gracefully_when_engine_construction_fails(monkeypatch):
    def _boom(params=None):
        raise RuntimeError("сеть недоступна — модель RapidOCR не скачать")

    _install_fake_rapidocr_module(monkeypatch, construct=_boom)
    reader = RapidOcrReader(sizes=(640,), label_size=0)

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
    reader = RapidOcrReader(sizes=(640,), label_size=0)

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
    reader = RapidOcrReader(sizes=(640,), label_size=0)

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
    reader = RapidOcrReader(sizes=(640, 960), label_size=0)

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
    reader = RapidOcrReader(sizes=(640, 960), label_size=0)

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))
    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert construct_count["n"] == 2  # по одному конструктору НА МАСШТАБ за всё время, не на read()


# --------------------------------------------------------------------------------------
# _resize_to_longest_side() — agents/H3-label-crop-ocr.md, задача 3, "Бикубика для
# МЕЛКИХ входов": уменьшает (делегирует _downscale_to_longest_side(), не тронут) ИЛИ
# увеличивает (PIL.Image.resize BICUBIC) — в отличие от _downscale_to_longest_side()
# (H2), которая НИКОГДА не увеличивает (её тесты выше не менялись).
# --------------------------------------------------------------------------------------


def test_resize_to_longest_side_no_op_exactly_at_target():
    arr = np.zeros((320, 640, 3), dtype=np.uint8)
    assert _resize_to_longest_side(arr, 640) is arr  # ровно на границе — тот же объект, без копии


def test_resize_to_longest_side_delegates_to_downscale_when_larger():
    arr = np.zeros((930, 1400, 3), dtype=np.uint8)
    assert _resize_to_longest_side(arr, 640).shape == _downscale_to_longest_side(arr, 640).shape
    assert _resize_to_longest_side(arr, 640).shape == (425, 640, 3)


def test_resize_to_longest_side_upscales_smaller_input_preserving_aspect_ratio():
    arr = np.zeros((50, 80, 3), dtype=np.uint8)
    result = _resize_to_longest_side(arr, 640)
    assert result.shape == (400, 640, 3)  # 80->640 (×8), 50×8=400 — аспект сохранён
    assert max(result.shape[:2]) == 640


def test_resize_to_longest_side_upscale_matches_pil_resize_bicubic_pixel_for_pixel():
    """Регресс-защита: ДОЛЖЕН быть `Image.resize(..., BICUBIC)`, не NEAREST/другой
    фильтр — та же дисциплина, что H2 уже применяет к downscale-пути (см.
    `test_downscale_to_longest_side_matches_pil_thumbnail_pixel_for_pixel` выше)."""
    from PIL import Image

    rng = np.random.default_rng(11)
    arr = rng.integers(0, 256, size=(50, 80, 3), dtype=np.uint8)

    result = _resize_to_longest_side(arr, 640)

    expected_im = Image.fromarray(arr, mode="RGB").resize((640, 400), Image.Resampling.BICUBIC)
    assert np.array_equal(result, np.asarray(expected_im))


def test_resize_to_longest_side_never_produces_zero_sized_dimension():
    """Экстремально узкий кроп (1px по одной стороне) — `max(1, round(...))`
    гарантирует минимум 1px на КОРОТКОЙ стороне после увеличения, не 0 (0 сломал бы
    PIL/движок дальше по конвейеру)."""
    arr = np.zeros((1, 500, 3), dtype=np.uint8)
    result = _resize_to_longest_side(arr, 640)
    assert result.shape[0] >= 1
    assert max(result.shape[:2]) == 640


# --------------------------------------------------------------------------------------
# Третий проход — CV_OCR_LABEL_SIZE / label_size (agents/H3-label-crop-ocr.md, задача 2)
# --------------------------------------------------------------------------------------


def test_default_label_size_is_1280():
    assert DEFAULT_LABEL_SIZE == 1280
    assert RapidOcrReader().label_size == 1280


def test_label_size_constructor_param_overrides_default():
    assert RapidOcrReader(label_size=960).label_size == 960


def test_label_size_zero_disables_via_constructor():
    assert RapidOcrReader(label_size=0).label_size == 0


def test_label_size_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("CV_OCR_LABEL_SIZE", "1600")
    assert RapidOcrReader().label_size == 1600


def test_label_size_env_var_overrides_constructor_param(monkeypatch):
    """Тот же приоритет, что `CV_OCR_RAPID_SIZES` над `rapid_sizes` в
    `cv.verify.LabelVerifier` — env побеждает явный аргумент конструктора."""
    monkeypatch.setenv("CV_OCR_LABEL_SIZE", "0")
    assert RapidOcrReader(label_size=1280).label_size == 0


def test_label_size_env_var_tolerates_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("CV_OCR_LABEL_SIZE", " 1280 \n")
    assert RapidOcrReader().label_size == 1280


class _StubLabelEngine(_StubRapidEngine):
    """Тот же двойник, что `_StubRapidEngine` — отдельное имя только для читаемости
    тестов третьего прохода (движок КОНКРЕТНО масштаба `label_size`)."""


def _box(x0, y0, x1, y1) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)


def _boxes(*items: np.ndarray) -> np.ndarray:
    """Собирает боксы в ОДИН numpy ndarray формы (N,4,2) — ИМЕННО так реальный
    `rapidocr` отдаёт `RapidOCROutput.boxes` (проверено эмпирически живым движком
    на приёмке 22.09, см. reports/h3-label-crop-ocr.md), НЕ список списков: живой
    прогон API поймал баг именно на этом расхождении типов (`not <ndarray с
    несколькими элементами>` -> "The truth value of an array with more than one
    element is ambiguous") — тесты этого файла обязаны воспроизводить РЕАЛЬНЫЙ
    тип, не удобный для написания тестов список."""
    return np.stack(items)


def test_label_pass_skipped_when_source_scale_finds_no_boxes():
    """Масштаб-источник (max(self.sizes)=960) отработал, но боксов не вернул
    (`None` — фото без читаемого текста в центральной колонке; РЕАЛЬНЫЙ `rapidocr`
    отдаёт именно `None`, не пустой список/массив, см. `_boxes()` докстринг выше) —
    третий проход не читается вовсе: движок label_size НЕ вызывается (хотя и
    конструируется — см. отдельный тест прогрева ниже)."""
    e960 = _StubRapidEngine(_StubRapidResult(txts=("ГДЕ",), scores=(0.9,), boxes=None))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("НЕ ДОЛЖНО ПОПАСТЬ",), scores=(0.9,)))
    reader = _reader_with_stub_engines({960: e960}, label_size=1280)
    reader._engines[1280] = label_engine

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "ГДЕ"
    assert label_engine.calls == []


def test_label_pass_skipped_when_source_scale_boxes_is_empty_ndarray():
    """Вариант `boxes` — ПУСТОЙ ndarray (не `None`, а форма (0,4,2)) — тоже
    законный вход (`len(boxes) == 0`), не должен падать на `not boxes`/`bool(...)`
    (см. `_boxes()` докстринг — ndarray с >1 элементом там падает, но и путь
    ПУСТОГО ndarray стоит проверить отдельно, раз производственный код обязан
    отличать его от `None` через `is None`/`len`, а не просто `not`)."""
    empty_boxes = np.empty((0, 4, 2), dtype=np.float32)
    e960 = _StubRapidEngine(_StubRapidResult(txts=("ГДЕ",), scores=(0.9,), boxes=empty_boxes))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("НЕ ДОЛЖНО ПОПАСТЬ",), scores=(0.9,)))
    reader = _reader_with_stub_engines({960: e960}, label_size=1280)
    reader._engines[1280] = label_engine

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "ГДЕ"
    assert label_engine.calls == []


def test_label_pass_boxes_as_real_multi_element_ndarray_does_not_raise():
    """Регресс-защита ИМЕННО живого бага (22.09): `boxes` — ndarray формы (2,4,2)
    (2 бокса, как реально отдаёт `rapidocr`) — `not boxes`/`bool(boxes)` на такой
    ndarray бросает ValueError ("ambiguous"), который `apps/api` бы поймал как
    validation_error/400 (см. `apps/api/app/routers/scan.py`). Без падения —
    третий проход обязан либо отработать, либо тихо отказаться, никогда не
    поднять исключение наружу `read()`."""
    two_boxes = _boxes(_box(280, 150, 360, 170), _box(300, 400, 340, 420))
    e960 = _StubRapidEngine(_StubRapidResult(txts=("ГДЕ",), scores=(0.9, 0.9), boxes=two_boxes))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("ЭТИКЕТКА",), scores=(0.9,)))
    reader = _reader_with_stub_engines({960: e960}, label_size=1280)
    reader._engines[1280] = label_engine

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))  # не должно поднять исключение

    assert "ГДЕ" in text.split()
    assert "ЭТИКЕТКА" in text.split()


def test_label_pass_skipped_when_source_scale_engine_missing(monkeypatch):
    """Масштаб-источник вообще не смог создать движок (см. `_engine_for()`
    деградацию — здесь через недоступный `rapidocr`) — `result is None` для
    этого масштаба, третий проход тоже пропускается (нет боксов, которые можно
    переиспользовать), хотя движок label_size сам по себе застейблен и рабочий."""
    monkeypatch.setitem(sys.modules, "rapidocr", None)  # 960 не сможет создать движок
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("НЕ ДОЛЖНО ПОПАСТЬ",), scores=(0.9,)))
    reader = RapidOcrReader(sizes=(960,), label_size=1280)
    reader._engines[1280] = label_engine

    with pytest.warns(UserWarning, match="rapidocr"):
        text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == ""
    assert label_engine.calls == []


def test_label_pass_appends_text_after_main_scales_separated_by_space():
    a = _box(280, 150, 360, 170)
    e640 = _StubRapidEngine(_StubRapidResult(txts=("ШАПКА",), scores=(0.9,), boxes=None))
    e960 = _StubRapidEngine(_StubRapidResult(txts=("НАЗВАНИЕ",), scores=(0.9,), boxes=_boxes(a)))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("ПОЛНЫЙ ТЕКСТ ЭТИКЕТКИ",), scores=(0.9,)))
    reader = _reader_with_stub_engines({640: e640, 960: e960}, label_size=1280)
    reader._engines[1280] = label_engine

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "ШАПКА НАЗВАНИЕ ПОЛНЫЙ ТЕКСТ ЭТИКЕТКИ"
    assert len(label_engine.calls) == 1


def test_label_pass_crops_from_full_resolution_source_not_downscaled_frame():
    """Ключевой тест координатной математики: боксы читаются в координатах
    ДАУНСКЕЙЛЕННОГО кадра масштаба-источника (640x320, из bottle_full 800x400
    через `_resize_to_longest_side`), `label_bbox()` возвращает прямоугольник в
    ТЕХ ЖЕ координатах — обязан быть смасштабирован ОБРАТНО (×800/640=1.25) и
    вырезан ИЗ bottle_full (полное разрешение, НЕ из уменьшенного кадра
    масштаба-источника). `label_size=360` подобран так, чтобы `_resize_to_
    longest_side()` перед движком НЕ трогал результат (max(128,360)==360==target,
    no-op) — тест проверяет ИМЕННО кроп-координаты, не отдельно уже
    протестированный ресайз."""
    bottle_full = (np.arange(400 * 800 * 3) % 256).astype(np.uint8).reshape(400, 800, 3)
    box_in_scaled_frame = _box(280, 150, 360, 170)  # координаты в кадре 640x320 (после ресайза 800->640)
    e640 = _StubRapidEngine(_StubRapidResult(txts=("X",), scores=(0.9,), boxes=_boxes(box_in_scaled_frame)))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("Y",), scores=(0.9,)))
    reader = _reader_with_stub_engines({640: e640}, label_size=360)
    reader._engines[360] = label_engine

    reader.read(bottle_full)

    assert len(label_engine.calls) == 1
    received = label_engine.calls[0]
    assert received.shape == (128, 360, 3)  # см. вычисление в докстринге теста выше
    assert np.array_equal(received, bottle_full[151:279, 220:580])


def test_label_pass_uses_boxes_from_largest_main_scale_not_smallest():
    """Источник боксов — `max(self.sizes)`, не первый/последний по порядку
    объявления: `sizes=(960, 640)` (960 объявлен ПЕРВЫМ) обязан всё равно взять
    боксы 960, не 640. Различимость гарантирована КОНСТРУКЦИЕЙ: `box_640` вне
    центральной колонки 640-кадра (cx=5 вне [115.2, 524.8]) — если бы код
    ошибочно использовал боксы 640, `label_bbox()` вернул бы `None` и "C" не
    попал бы в текст вовсе; `box_960` — валидный кандидат для 960-кадра."""
    box_640 = _box(0, 0, 10, 10)
    box_960 = _box(280, 150, 360, 170)
    e640 = _StubRapidEngine(_StubRapidResult(txts=("A",), scores=(0.9,), boxes=_boxes(box_640)))
    e960 = _StubRapidEngine(_StubRapidResult(txts=("B",), scores=(0.9,), boxes=_boxes(box_960)))
    label_engine = _StubLabelEngine(_StubRapidResult(txts=("C",), scores=(0.9,)))
    reader = _reader_with_stub_engines({960: e960, 640: e640}, label_size=1280)
    reader._engines[1280] = label_engine

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert "C" in text.split()  # третий проход отработал -> нашёл боксы (значит, взял 960, не 640)
    assert len(label_engine.calls) == 1


def test_label_size_zero_disables_pass_entirely_no_engine_lookup():
    """`label_size=0` — третий проход не пытается получить движок ВООБЩЕ (не
    только "не вызывает") — застейбленный движок для 1280 остаётся нетронутым
    и НЕ регистрируется в `_engines`, если сам явно не добавлен."""
    a = _box(280, 150, 360, 170)
    e960 = _StubRapidEngine(_StubRapidResult(txts=("B",), scores=(0.9,), boxes=_boxes(a)))
    reader = _reader_with_stub_engines({960: e960}, label_size=0)

    text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "B"
    assert 1280 not in reader._engines


def test_label_pass_call_failure_degrades_to_empty_without_affecting_main_text():
    a = _box(280, 150, 360, 170)
    e960 = _StubRapidEngine(_StubRapidResult(txts=("B",), scores=(0.9,), boxes=_boxes(a)))
    label_engine = _StubLabelEngine(raises=RuntimeError("движок этикетки упал"))
    reader = _reader_with_stub_engines({960: e960}, label_size=1280)
    reader._engines[1280] = label_engine

    with pytest.warns(UserWarning, match="1280"):
        text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "B"


def test_label_engine_construction_is_attempted_even_when_no_boxes_found(monkeypatch):
    """Прогрев (agents/H3-label-crop-ocr.md, докстринг класса): движок `label_size`
    обязан конструироваться ВСЕГДА при `label_size > 0`, даже когда `label_bbox()`
    ничего не находит (например, 2x2-плейсхолдер `warm_up_label_verifier()`) — иначе
    первый боевой запрос с настоящей этикеткой платит холодный старт третьего
    движка. Проверено через ПОДМЕНУ реального пакета `rapidocr` (не прямой инжект
    в `_engines`, как остальные тесты этого файла) — только так видно, ПЫТАЛСЯ ли
    код вообще создать движок для 1280, а не просто нашёл готовый."""
    captured_sizes: list[int] = []

    def _capture(params=None):
        captured_sizes.append(params["Det.limit_side_len"])
        return _StubRapidEngine(_StubRapidResult(txts=(), scores=(), boxes=None))

    _install_fake_rapidocr_module(monkeypatch, construct=_capture)
    reader = RapidOcrReader(sizes=(960,), label_size=1280)

    reader.read(np.zeros((10, 10, 3), dtype=np.uint8))

    assert set(captured_sizes) == {960, 1280}


def test_label_engine_construction_failure_degrades_without_affecting_main_text(monkeypatch):
    def _boom_only_label(params=None):
        if params["Det.limit_side_len"] == 1280:
            raise RuntimeError("модель 1280px недоступна")
        return _StubRapidEngine(_StubRapidResult(txts=("B",), scores=(0.9,), boxes=_boxes(_box(280, 150, 360, 170))))

    _install_fake_rapidocr_module(monkeypatch, construct=_boom_only_label)
    reader = RapidOcrReader(sizes=(960,), label_size=1280)

    with pytest.warns(UserWarning, match="1280"):
        text = reader.read(np.zeros((400, 800, 3), dtype=np.uint8))

    assert text == "B"


# --------------------------------------------------------------------------------------
# 22.09: пороги детектора (строки вразрядку) — дефолты 0.3/2.0, env CV_OCR_DET_BOX_THRESH/UNCLIP
# --------------------------------------------------------------------------------------


def test_engine_params_carry_sensitive_detector_thresholds_by_default(monkeypatch):
    seen = {}

    def construct(params=None):
        seen.update(params or {})
        return _StubRapidEngine(_StubRapidResult((), ()))

    _install_fake_rapidocr_module(monkeypatch, construct=construct)
    monkeypatch.delenv("CV_OCR_DET_BOX_THRESH", raising=False)
    monkeypatch.delenv("CV_OCR_DET_UNCLIP", raising=False)
    reader = RapidOcrReader(sizes=(640,), label_size=0)
    assert reader._engine_for(640) is not None
    assert seen["Det.box_thresh"] == pytest.approx(0.3)
    assert seen["Det.unclip_ratio"] == pytest.approx(2.0)


def test_engine_params_detector_thresholds_from_env(monkeypatch):
    seen = {}

    def construct(params=None):
        seen.update(params or {})
        return _StubRapidEngine(_StubRapidResult((), ()))

    _install_fake_rapidocr_module(monkeypatch, construct=construct)
    monkeypatch.setenv("CV_OCR_DET_BOX_THRESH", "0.6")
    monkeypatch.setenv("CV_OCR_DET_UNCLIP", "1.5")
    reader = RapidOcrReader(sizes=(640,), label_size=0)
    assert reader._engine_for(640) is not None
    assert seen["Det.box_thresh"] == pytest.approx(0.6)
    assert seen["Det.unclip_ratio"] == pytest.approx(1.5)
