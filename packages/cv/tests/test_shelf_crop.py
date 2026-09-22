"""Тесты `cv/shelf_crop.py` (agents/ML-2-shelf-crop.md) — сегментация кадра полки,
перенесённая из `qa/real_photos_shelf_crop.py` (ml-lead, reports/ml-lead-shelf-crop.md).

Без сети и без тяжёлых моделей (бриф ml-engineer): `segment_boxes()` — чистая
геометрия по готовым боксам, реального RapidOCR не касается вовсе. `ShelfDetector`/
`detect_boxes()`/`segment_shelf()` тестируются С ПОДМЕНЁННЫМ движком — та же
дисциплина, что `test_ocr_rapid.py` уже применяет к `cv.ocr_rapid.RapidOcrReader`
(фейковый объект вместо реального импорта, либо `sys.modules["rapidocr"]`).
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from cv.shelf_crop import (
    DEFAULT_EXPECTED_BOTTLE_WIDTH_FRAC,
    DEFAULT_MAX_BOTTLE_WIDTH_FRAC,
    DEFAULT_MIN_BOXES,
    DEFAULT_MIN_TEXT_ASPECT,
    ShelfDetector,
    ShelfSegmentation,
    _split_oversized_segments,
    detect_boxes,
    expand_from_center,
    geometric_columns,
    intervals_xy,
    run_clusters,
    segment_boxes,
    segment_shelf,
    voronoi_bounds,
)


def box(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    """4-точечный полигон, как отдаёт `rapidocr` (`RapidOCROutput.boxes`) — тот же
    хелпер, что `test_label_crop.py::box()`."""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def full_height_boxes(xs: list[tuple[float, float]], h: float) -> np.ndarray:
    """Боксы, растянутые НА ВСЮ высоту кадра (y0=0, y1=h) — изолирует тесты
    гейта/колонок от эвристики поиска РЯДА (`expand_from_center`, тестируется
    отдельно ниже): при полной высоте `row_mask=(y1>ry0)&(y0<ry1)` истинен для
    любого положения окна ряда, все боксы гарантированно попадают в n_row_boxes."""
    return np.array([box(x0, 0, x1, h) for x0, x1 in xs], dtype=np.float32)


W, H = 1000, 2000


# --------------------------------------------------------------------------------------
# intervals_xy — тривиальная геометрия
# --------------------------------------------------------------------------------------


def test_intervals_xy_min_max_regardless_of_point_order():
    boxes = np.array([box(10, 20, 110, 220)], dtype=np.float32)
    x0, y0, x1, y1 = intervals_xy(boxes)
    assert (x0[0], y0[0], x1[0], y1[0]) == pytest.approx((10.0, 20.0, 110.0, 220.0))


# --------------------------------------------------------------------------------------
# run_clusters — реальные разрывы НУЛЕВОГО покрытия (bins=100 -> bin_w=10, целые числа)
# --------------------------------------------------------------------------------------


def test_run_clusters_empty_input_returns_empty_list():
    assert run_clusters(np.array([]), np.array([]), 1000) == []


def test_run_clusters_two_well_separated_boxes_form_two_clusters():
    a0, a1 = np.array([100.0, 700.0]), np.array([300.0, 900.0])
    clusters = run_clusters(a0, a1, 1000, bins=100)
    assert [c["center"] for c in clusters] == pytest.approx([200.0, 800.0])
    assert [(c["a0"], c["a1"]) for c in clusters] == pytest.approx([(100.0, 300.0), (700.0, 900.0)])


def test_run_clusters_merges_boxes_within_min_gap():
    """Разрыв 20px (2 бина по 10px) РОВНО на пороге `min_gap_frac=0.02*100 бинов=2` —
    сливается в один кластер (`<=`, не `<`)."""
    a0, a1 = np.array([100.0, 320.0]), np.array([300.0, 500.0])
    clusters = run_clusters(a0, a1, 1000, bins=100)
    assert len(clusters) == 1
    assert (clusters[0]["a0"], clusters[0]["a1"]) == pytest.approx((100.0, 500.0))


def test_run_clusters_does_not_merge_boxes_beyond_min_gap():
    a0, a1 = np.array([100.0, 340.0]), np.array([300.0, 500.0])  # разрыв 40px = 4 бина > 2
    clusters = run_clusters(a0, a1, 1000, bins=100)
    assert len(clusters) == 2


def test_run_clusters_single_wide_box_is_one_cluster():
    clusters = run_clusters(np.array([50.0]), np.array([950.0]), 1000, bins=100)
    assert len(clusters) == 1
    assert clusters[0]["center"] == pytest.approx(500.0, abs=10)


# --------------------------------------------------------------------------------------
# voronoi_bounds — раздел БЕЗ нахлёста, ровно на полпути между центрами
# --------------------------------------------------------------------------------------


def test_voronoi_bounds_two_centers():
    assert voronoi_bounds([200.0, 800.0], 0.0, 1000.0) == [(0.0, 500.0), (500.0, 1000.0)]


def test_voronoi_bounds_three_centers_no_overlap():
    bounds = voronoi_bounds([100.0, 400.0, 900.0], 0.0, 1000.0)
    assert bounds == [(0.0, 250.0), (250.0, 650.0), (650.0, 1000.0)]
    # смежные границы совпадают побитово — раздел без нахлёста и без зазора
    for (_, b1), (a2, _) in zip(bounds, bounds[1:]):
        assert b1 == a2


def test_voronoi_bounds_single_center_spans_whole_axis():
    assert voronoi_bounds([500.0], 0.0, 1000.0) == [(0.0, 1000.0)]


# --------------------------------------------------------------------------------------
# expand_from_center — граница ряда вокруг Y-центра кадра
# --------------------------------------------------------------------------------------


def test_expand_from_center_finds_gap_around_dense_band():
    """Плотная полоса боксов вокруг центра оси (bins=100, окно поиска 40%) —
    граница ряда обязана лечь ВНУТРИ окна поиска и ПОКРЫВАТЬ полосу боксов
    (не обрезать её), не выходя за пределы кадра."""
    n = 40
    y0 = np.full(n, 450.0)
    y1 = np.full(n, 550.0)
    top, bot = expand_from_center(y0, y1, 1000.0, bins=100)
    assert 0.0 <= top <= 450.0
    assert 550.0 <= bot <= 1000.0


def test_expand_from_center_finds_structural_break_between_two_shelves():
    """Целевой ряд у центра кадра [400,600] + соседняя полка ВЫШЕ [100,200] с
    чистым разрывом между ними [200,400] — граница обязана лечь ВНУТРИ этого
    разрыва (не резать целевой ряд, не заезжать в соседнюю полку) — ровно то,
    для чего эвристика написана (reports/ml-lead-shelf-crop.md, "Метод", п.2)."""
    y0 = np.array([400.0] * 40 + [100.0] * 40)
    y1 = np.array([600.0] * 40 + [200.0] * 40)
    top, bot = expand_from_center(y0, y1, 1000.0, bins=100)
    assert 200.0 < top < 400.0  # внутри разрыва, не внутри целевого ряда и не внутри соседней полки
    assert bot >= 600.0  # не обрезает целевой ряд снизу (соседей снизу нет — граница уходит до края поиска)


def test_expand_from_center_does_not_bleed_into_shelf_on_either_side():
    """Соседние полки И сверху, И снизу — окно ряда обязано остаться СТРОГО
    между ними по обе стороны одновременно."""
    y0 = np.array([400.0] * 40 + [100.0] * 40 + [800.0] * 40)
    y1 = np.array([600.0] * 40 + [200.0] * 40 + [900.0] * 40)
    top, bot = expand_from_center(y0, y1, 1000.0, bins=100)
    assert 200.0 < top < 400.0
    assert 600.0 < bot < 800.0


def test_expand_from_center_no_boxes_returns_full_search_window():
    """Нет плотности вовсе (все `a0==a1==0`, что математически невозможно для
    реальных боксов, но проверяет вырожденный случай без деления на ноль) —
    функция не падает и возвращает валидный диапазон внутри кадра."""
    top, bot = expand_from_center(np.zeros(5), np.zeros(5), 1000.0, bins=100)
    assert 0.0 <= top <= bot <= 1000.0


# --------------------------------------------------------------------------------------
# geometric_columns — Sobel-фолбэк без модели
# --------------------------------------------------------------------------------------


def _synthetic_band_with_gaps(w: int, h: int, gaps: list[tuple[int, int]]) -> np.ndarray:
    """Полоса h x w в градациях серого: высокочастотный (случайный) паттерн
    "бутылок" и ПОСТОЯННЫЙ серый в промежутках `gaps` (список (x0, x1)) —
    Sobel-X внутри промежутка ~0 (нет вертикальных границ), у "бутылок" —
    заметно выше (много случайных перепадов)."""
    rng = np.random.default_rng(0)
    band = rng.integers(0, 256, size=(h, w)).astype(np.uint8)
    for x0, x1 in gaps:
        band[:, x0:x1] = 128
    return band


def test_geometric_columns_finds_gaps_between_noisy_bottles():
    """`max_bottle_width_frac` намеренно ослаблен (10.0 — не сработает ни при
    каком реальном сегменте на этом маленьком кадре): тест — про НАХОЖДЕНИЕ
    разрывов, дробление сегментов по ширине бутылки проверяется отдельно
    (`test_split_oversized_segments_*` ниже) — маленький `h=60` в этом
    фикстуре иначе тривиально считал бы КАЖДЫЙ сегмент "шире бутылки"."""
    w, h = 300, 60
    gaps = [(95, 115), (195, 215)]
    gray = _synthetic_band_with_gaps(w, h, gaps)
    segs = geometric_columns(gray, 0, h, w, max_bottle_width_frac=10.0)
    assert len(segs) == 3  # 2 разрыва -> 3 сегмента-"бутылки"
    # середины разрывов должны попасть МЕЖДУ сегментами, не внутрь них
    assert segs[0][1] <= 115
    assert segs[1][0] >= 95 and segs[1][1] <= 215
    assert segs[2][0] >= 195


def test_geometric_columns_returns_empty_when_fewer_than_two_segments_survive():
    """Идеально ровный фон без единой вертикальной границы (нулевая энергия
    Sobel-X ВЕЗДЕ) — фолбэк честно не находит структуры, отдаёт `[]` (сигнал
    "колонок нет", как и малое число OCR-кластеров)."""
    band = np.full((60, 300), 128, dtype=np.uint8)
    assert geometric_columns(band, 0, 60, 300) == []


def test_geometric_columns_band_too_thin_returns_empty():
    gray = np.zeros((2, 300), dtype=np.uint8)
    assert geometric_columns(gray, 0, 2, 300) == []


# --------------------------------------------------------------------------------------
# _split_oversized_segments / geometric_columns — qa-manual, reports/qa-manual-field-
# bottles.md: F02/F10 — плотные ряды ОДИНАКОВЫХ бутылок без текста между ними, Sobel-
# фолбэк оставлял 2-3 ШИРОКИХ сегмента (до 1829px при высоте ряда 1416px) вместо 8-10
# узких — 25/85 кропов брака сегментации, ЦЕЛИКОМ из этих двух фото.
# --------------------------------------------------------------------------------------


def test_split_oversized_segments_leaves_normal_width_segment_untouched():
    row_h = 1000.0  # max_w = 0.4*1000 = 400
    segs = [(0.0, 300.0)]
    assert _split_oversized_segments(segs, row_h) == segs


def test_split_oversized_segments_splits_wide_segment_into_equal_parts():
    """row_h=1000 -> max_w=400, expected_w=320: сегмент 960px (ровно 3x
    expected) -> 3 РАВНЫЕ части по 320px, покрывающие исходный диапазон без
    дыр и нахлёста."""
    segs = _split_oversized_segments([(100.0, 1060.0)], 1000.0)
    assert segs == pytest.approx([(100.0, 420.0), (420.0, 740.0), (740.0, 1060.0)])


def test_split_oversized_segments_enforces_minimum_two_parts():
    """Сегмент чуть шире `max_w`, но `round(width/expected_w)` дал бы 1 —
    результат всё равно >= 2 частей (сегмент УЖЕ признан "не одной бутылкой",
    оставлять его целым нельзя)."""
    row_h = 1000.0  # max_w=400, expected_w=320
    segs = _split_oversized_segments([(0.0, 410.0)], row_h)  # 410 > max_w=400, но round(410/320)=1
    assert len(segs) == 2
    assert segs[0][0] == 0.0 and segs[-1][1] == pytest.approx(410.0)


def test_split_oversized_segments_only_splits_the_oversized_one():
    row_h = 1000.0  # max_w=400
    segs = _split_oversized_segments([(0.0, 200.0), (200.0, 1160.0)], row_h)
    assert segs[0] == (0.0, 200.0)  # первый — в пределах порога, не тронут
    assert len(segs) > 2  # второй (960px) — раздроблен


def test_split_oversized_segments_degenerate_row_height_is_noop():
    segs = [(0.0, 500.0)]
    assert _split_oversized_segments(segs, 0.0) == segs
    assert _split_oversized_segments(segs, -10.0) == segs


def test_split_oversized_segments_custom_fractions_respected():
    """Явные `max_frac`/`expected_frac` (не дефолт 0.4/0.32) — порог и число
    частей считаются от ПЕРЕДАННЫХ значений, не от констант модуля."""
    segs = _split_oversized_segments([(0.0, 100.0)], row_h=100.0, max_frac=0.5, expected_frac=0.25)
    # max_w=50 < 100 -> дробим; expected_w=25 -> round(100/25)=4 части по 25px
    assert len(segs) == 4
    assert all(pytest.approx(b - a) == 25.0 for a, b in segs)


def test_geometric_columns_splits_oversized_segments_by_default():
    """Ряд НИЗКИЙ относительно найденных промежутков (`h=60` -> `max_w=24px`,
    любой "естественный" сегмент шире) — качественно воспроизводит F02/F10:
    без дробления получались бы 3 сегмента по ~90-100px, каждый заведомо шире
    одной бутылки на этом отношении ширина/высота ряда."""
    w, h = 300, 60
    gaps = [(95, 115), (195, 215)]
    gray = _synthetic_band_with_gaps(w, h, gaps)
    segs = geometric_columns(gray, 0, h, w)  # дефолтные max_bottle_width_frac/expected_bottle_width_frac
    assert len(segs) > 3  # раздроблено мельче, чем "просто 3 промежутка"
    max_w = DEFAULT_MAX_BOTTLE_WIDTH_FRAC * h
    assert all(b - a <= max_w + 1e-6 for a, b in segs)  # ни один итоговый сегмент не превышает порог
    assert segs[0][0] == 0.0
    assert segs[-1][1] == pytest.approx(w, abs=1)


def test_geometric_columns_does_not_split_when_row_is_tall_enough():
    """Тот же разрыв, но `h` увеличен так, что найденные сегменты УЖЕ в
    пределах `max_bottle_width_frac` — дробление не требуется, сегментов
    ровно 3 (как без правки qa-manual)."""
    w, h = 300, 260  # max_w = 0.4*260 = 104 >= типичная ширина сегмента (~90-100px)
    gaps = [(95, 115), (195, 215)]
    gray = _synthetic_band_with_gaps(w, h, gaps)
    segs = geometric_columns(gray, 0, h, w)
    assert len(segs) == 3


# --------------------------------------------------------------------------------------
# segment_boxes — сценарии брифа: один блок / два кластера >= min_boxes / редкие боксы
# --------------------------------------------------------------------------------------


def test_single_block_of_boxes_is_not_a_shelf_frame_unchanged():
    """Один блок текстовых боксов (одна бутылка организаторского фото: герб +
    название могут разойтись по X, но `run_clusters` не находит НУЛЕВОГО разрыва
    между ними) — колонок < 2 -> без `gray` фолбэк недоступен -> `is_shelf=False`,
    `crops` — ровно весь кадр (contracts: регрессия на не-полочных фото структурно
    невозможна)."""
    boxes = full_height_boxes([(400, 450), (420, 520), (460, 600)], H)  # перекрывающиеся/смежные по X
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES)
    assert seg.is_shelf is False
    assert seg.crops == ((0, 0, W, H),)
    assert seg.center_index == 0
    assert seg.col_method == "none"
    assert seg.n_boxes == 3


def test_two_separated_clusters_with_enough_boxes_triggers_gate_and_splits_at_midpoint():
    """>= 30 боксов в ряду, ДВЕ ясно разделённые X-колонки — гейт срабатывает,
    раздел Вороного — ровно на полпути между центрами колонок, `center_index`
    указывает на колонку, ближайшую к X-центру кадра.

    `min_text_aspect=0.0` — тест про раздел Вороного (v1), не про гейт v2:
    `full_height_boxes` даёт row_height=800 (см. `test_shelf_crop.py`, гейт v2
    ниже), а текст здесь узкий (охват 745px, аспект 0.93 < 1.2 по умолчанию) —
    без сброса гейт v2 срезал бы этот сценарий, тест изолирован от него."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(16)]  # x в [50,125], центр колонки ~ 87.5
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(16)]  # x в [700,795], центр ~ 747.5
    boxes = full_height_boxes(left_xs + right_xs, H)
    assert len(boxes) == 32
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.is_shelf is True
    assert seg.n_row_boxes == 32
    assert seg.col_method == "ocr"
    assert len(seg.crops) == 2
    left, right = seg.crops
    midpoint = (left[2] + right[0]) / 2  # раздел без нахлёста — границы совпадают
    assert left[2] == right[0]
    # правая колонка (центр ~747) ближе к центру кадра (500), чем левая (центр ~87)
    assert seg.center_index == 1
    assert seg.central_crop == right


def test_sparse_boxes_below_min_boxes_threshold_does_not_trigger_gate():
    """ДВЕ ясно разделённые колонки, но боксов в ряду МЕНЬШЕ `min_boxes` (29 < 30) —
    гейт не срабатывает несмотря на валидные >= 2 колонки (порог по боксам —
    самостоятельное, обязательное условие, см. reports/ml-lead-shelf-crop.md,
    "Гейт": без него ложные 2-4 "колонки" находятся у ОДНОЙ бутылки)."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(14)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(15)]  # итого 29 < 30
    boxes = full_height_boxes(left_xs + right_xs, H)
    assert len(boxes) == 29
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES)
    assert seg.is_shelf is False
    assert seg.crops == ((0, 0, W, H),)
    assert seg.n_row_boxes == 29
    assert seg.col_method == "ocr"  # колонки геометрически найдены, гейт всё равно не пройден


def test_exactly_min_boxes_threshold_is_inclusive():
    """`min_text_aspect=0.0` — тест изолирует порог по числу боксов (v1), не
    гейт v2 (тот же узкий паттерн 0.93 < 1.2, что и тест раздела Вороного выше)."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(15)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(15)]  # ровно 30
    boxes = full_height_boxes(left_xs + right_xs, H)
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.n_row_boxes == 30
    assert seg.is_shelf is True  # >= — включительно


def test_no_boxes_at_all_is_not_a_shelf():
    seg = segment_boxes(np.zeros((0, 4, 2), dtype=np.float32), W, H)
    assert seg.is_shelf is False
    assert seg.crops == ((0, 0, W, H),)
    assert seg.col_method == "none"
    assert seg.n_boxes == 0
    assert seg.n_row_boxes == 0


def _row_repeated_gap_band(w: int, h: int, gaps: list[tuple[int, int]], seed: int = 0) -> np.ndarray:
    """Тот же принцип, что `_synthetic_band_with_gaps`, но ОДИН случайный
    "бутылочный" паттерн повторяется на каждую строку (`np.tile`) — делает
    результат `geometric_columns()` независимым от того, КАКОЙ именно диапазон
    строк (ry0:ry1) выберет `expand_from_center()` внутри `segment_boxes()`
    (не нужно предсказывать эвристику ряда, чтобы детерминированно проверить
    геометрический фолбэк колонок)."""
    rng = np.random.default_rng(seed)
    row = rng.integers(0, 256, size=w).astype(np.uint8)
    for x0, x1 in gaps:
        row[x0:x1] = 128
    return np.tile(row, (h, 1))


def test_geometric_fallback_used_when_ocr_columns_insufficient():
    """< 2 OCR-колонок (один плотный блок боксов по центру — как этикетка одной
    бутылки), но `gray` даёт Sobel-фолбэку найти структурные разрывы —
    `col_method` переключается на "geometric", гейт может сработать через него.
    Точное число итоговых сегментов не фиксируем (см. `test_split_oversized_*`
    ниже — сегменты шире `DEFAULT_MAX_BOTTLE_WIDTH_FRAC` доли высоты ряда
    дополнительно дробятся, реальное число зависит от найденного окна ряда).

    `min_text_aspect=0.0` — тест про геометрический фолбэк колонок (v1): все 30
    OCR-боксов совпадают (120-180), охват текста узкий (60px) — гейт v2 срезал
    бы этот сценарий, что здесь не проверяется (изолировано отдельным тестом)."""
    fw, fh = 300, 600
    boxes = full_height_boxes([(120.0, 180.0)] * 30, fh)  # 30 совпадающих боксов — один OCR-блок
    gray = _row_repeated_gap_band(fw, fh, [(95, 115), (195, 215)])
    seg = segment_boxes(boxes, fw, fh, gray=gray, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.col_method == "geometric"
    assert seg.is_shelf is True
    assert len(seg.crops) >= 2
    assert 0 <= seg.center_index < len(seg.crops)
    left, right = seg.central_crop[0], seg.central_crop[2]
    assert left < 150 < right  # центральный кроп реально накрывает X-центр кадра (150)


def test_gate_false_bypasses_threshold_but_still_needs_two_columns():
    """`gate=False` (напр. исследовательский `--always`) — не проверяет
    `is_shelf`, но БЕЗ >= 2 колонок фолбэк всё равно один "кроп" — полоса ряда
    целиком, не весь кадр (боевой код НИКОГДА не зовёт с `gate=False`, см.
    apps/api/app/cv/service.py — тест документирует прототип-режим сравнения)."""
    boxes = full_height_boxes([(400, 450), (420, 520)], H)
    seg = segment_boxes(boxes, W, H, gate=False, min_boxes=DEFAULT_MIN_BOXES)
    assert seg.is_shelf is False  # флаг статуса не врёт, даже когда гейт пропущен
    assert len(seg.crops) == 1
    assert seg.crops[0][0] == 0 and seg.crops[0][2] == W  # полная ширина, но НЕ обязательно полная высота


def test_gate_false_with_enough_shelf_still_returns_split_crops():
    """`min_text_aspect=0.0` на ОБОИХ вызовах — иначе `seg_gated.is_shelf`
    стало бы `False` (узкий текст, аспект 0.93 < 1.2) и вернуло бы весь кадр,
    а `seg_ungated` (гейт пропущен) — по-прежнему разрез, разваливая само
    сравнение, которое этот тест проверяет."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(16)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(16)]
    boxes = full_height_boxes(left_xs + right_xs, H)
    seg_gated = segment_boxes(boxes, W, H, gate=True, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    seg_ungated = segment_boxes(boxes, W, H, gate=False, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg_gated.crops == seg_ungated.crops  # гейт пройден -> оба пути совпадают


def test_three_columns_center_index_picks_nearest_to_frame_center():
    """`min_text_aspect=0.0` — тест про выбор `center_index` среди 3 колонок
    (v1), не про гейт v2 (охват текста здесь 892px/800=1.115, чуть НИЖЕ 1.2)."""
    xs = [(50.0 + 3 * i, 65.0 + 3 * i) for i in range(10)]  # колонка ~ x=57..80, center~68
    xs += [(460.0 + 3 * i, 475.0 + 3 * i) for i in range(10)]  # колонка центр ~478 (ближе к 500)
    xs += [(900.0 + 3 * i, 915.0 + 3 * i) for i in range(10)]  # колонка центр ~918
    boxes = full_height_boxes(xs, H)
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.is_shelf is True
    assert len(seg.crops) == 3
    assert seg.center_index == 1  # средняя колонка ближе всего к W/2=500


def test_candidate_indices_defaults_to_center_only_when_not_borderline():
    """`min_text_aspect=0.0` — тест про кандидатов НЕ на границе (v1), не про
    гейт v2; без сброса `is_shelf` ушёл бы в `False` по гейту v2 (аспект 0.93),
    и `candidate_indices==(center_index,)` совпало бы случайно (оба `(0,)` из
    ветки "гейт не пройден"), а не по проверяемой логике."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(16)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(16)]
    boxes = full_height_boxes(left_xs + right_xs, H)
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.candidate_indices == (seg.center_index,)


def test_candidate_indices_includes_immediate_neighbor_when_nearly_tied():
    """Три колонки, где ДВЕ соседние колонки (B, C) почти одинаково близки к
    X-центру кадра (разница расстояний 20 <= `border_tie_frac*W`=60) — обе
    становятся кандидатами (reports/ml-lead-shelf-crop.md, риски, п.2:
    off-by-one на пограничном разделе Вороного, F04/F30). Колонка A — далеко,
    не участвует.

    `min_text_aspect=0.0` — тест про `candidate_indices` (v1), охват текста
    (510px/800=0.64) — ниже гейта v2, изолируем его."""
    a = [(50.0, 90.0)] * 10  # центр 70, dist от центра кадра (500) = 430
    b = [(420.0, 470.0)] * 10  # центр 445, dist = 55
    c = [(510.0, 560.0)] * 10  # центр 535, dist = 35 — ближе всех, но близко к b
    boxes = full_height_boxes(a + b + c, H)
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, border_tie_frac=0.06, min_text_aspect=0.0)
    assert len(seg.crops) == 3
    assert seg.center_index == 2  # колонка c — ближайшая к центру кадра
    assert seg.candidate_indices == (1, 2)  # b — непосредственный сосед, почти той же дистанции


def test_candidate_indices_excludes_non_adjacent_or_far_column():
    """Кандидат добавляется, только если он НЕПОСРЕДСТВЕННЫЙ сосед по порядку
    колонок И достаточно близок — далёкая третья колонка не попадает, даже если
    гипотетически оказалась бы второй по близости (здесь этого не происходит,
    тест фиксирует инвариант "далёкая колонка не добавляется").

    `min_text_aspect=0.0` — тест про `candidate_indices` (v1); охват текста
    здесь 920px/800=1.15, чуть НИЖЕ гейта v2 по умолчанию (1.2)."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(11)]
    mid_xs = [(480.0 + 5 * i, 500.0 + 5 * i) for i in range(11)]
    right_xs = [(900.0 + 5 * i, 920.0 + 5 * i) for i in range(11)]
    boxes = full_height_boxes(left_xs + mid_xs + right_xs, H)
    seg = segment_boxes(boxes, W, H, min_boxes=DEFAULT_MIN_BOXES, border_tie_frac=0.06, min_text_aspect=0.0)
    assert seg.center_index == 1  # средняя колонка — центр кадра
    assert seg.candidate_indices == (1,)  # ни левая, ни правая не в пределах border_tie_frac


# --------------------------------------------------------------------------------------
# Гейт v2 — text_aspect (agents/ML-3-shelf-gate.md, reports/ml-lead-shelf-gate-v2.md):
# доп. сигнал против ложного срабатывания v1 на ОДНОЙ бутылке со сложной вёрсткой
# этикетки (регрессия ML-2, `87.88_28-08-2026_16-56-20.webp`, reports/ml-eng-ml2.md).
# Свой локальный кадр FW=1000/FH=1000 (не модульный W/H=1000/2000 выше) — с
# `full_height_boxes` `expand_from_center` даёт `row_height = 0.4*кадра` детерминированно
# (проверено эмпирически: плоский профиль плотности -> argmin тай-брейк всегда на первом
# индексе окна поиска), FH=1000 -> row_height=400 даёт круглые числа охвата текста.
# --------------------------------------------------------------------------------------

FW, FH = 1000, 1000
ROW_HEIGHT_FULL = 0.4 * FH  # = 400, см. комментарий выше


def test_text_aspect_regression_pattern_below_default_threshold_is_not_a_shelf():
    """Синтетика формы "87.88" (reports/ml-eng-ml2.md): ОДИН ряд, >= 30 боксов,
    2 X-кластера (реальный OCR-разрыв, gap=210px >> порога слияния ~20px) — как
    будто одна этикетка, обе половины которой разъехались по X, а не 2 бутылки —
    но охват текста УЗКИЙ относительно высоты ряда: [300,395]+[605,700] -> ширина
    400 / row_height 400 = aspect 1.00 < 1.2 (реальный кейс — 1.06, тот же
    порядок). C ДЕФОЛТНЫМ порогом гейт v2 обязан исключить такой кадр, хотя v1
    (колонки>=2 И боксов>=30) сам по себе сработал бы (см. тест ниже)."""
    left_xs = [(300.0 + 5 * i, 320.0 + 5 * i) for i in range(16)]  # span [300,395]
    right_xs = [(605.0 + 5 * i, 625.0 + 5 * i) for i in range(16)]  # span [605,700], gap=210
    boxes = full_height_boxes(left_xs + right_xs, FH)
    assert len(boxes) == 32  # >= DEFAULT_MIN_BOXES

    seg = segment_boxes(boxes, FW, FH, min_boxes=DEFAULT_MIN_BOXES)  # min_text_aspect по умолчанию (1.2)
    assert seg.text_aspect == pytest.approx(1.0)
    assert seg.col_method == "ocr"  # v1 честно нашёл 2 колонки...
    assert seg.n_row_boxes == 32  # ...и боксов достаточно...
    assert seg.is_shelf is False  # ...но гейт v2 всё равно исключает («не полка»)
    assert seg.crops == ((0, 0, FW, FH),)  # весь кадр, побитово (см. докстринг модуля, "Алгоритм", п.4)


def test_text_aspect_zero_threshold_reproduces_old_v1_only_behavior_on_same_pattern():
    """ТОТ ЖЕ паттерн, что в тесте выше, но `min_text_aspect=0.0` (эквивалент
    старого поведения ДО этого брифа, когда сигнала text_aspect не было вовсе) —
    `is_shelf` переключается на `True`, доказывая, что именно НОВЫЙ сигнал
    (не случайное совпадение с чем-то другим в v1) заблокировал регрессию
    в тесте выше: единственная разница между двумя тестами — этот порог."""
    left_xs = [(300.0 + 5 * i, 320.0 + 5 * i) for i in range(16)]
    right_xs = [(605.0 + 5 * i, 625.0 + 5 * i) for i in range(16)]
    boxes = full_height_boxes(left_xs + right_xs, FH)

    seg = segment_boxes(boxes, FW, FH, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.text_aspect == pytest.approx(1.0)  # тот же аспект, что и выше — сигнал не изменился
    assert seg.is_shelf is True  # но гейт v2 обнулён -> старое поведение v1
    assert len(seg.crops) == 2


def test_text_aspect_wide_multi_bottle_row_still_passes_default_gate():
    """Контрольный "проход": широкий ряд, 3 РЕАЛЬНО разделённые колонки (как
    несколько бутылок подряд на настоящей полке), охват текста ШИРОКИЙ
    относительно высоты ряда: [50,145]+[475,570]+[855,950] -> ширина 900 /
    row_height 400 = aspect 2.25 >= 1.2 — гейт v2 НЕ должен резать настоящую
    полку своим дефолтным порогом (v1 и v2 оба «за», без переопределения
    `min_text_aspect`, в отличие от узких паттернов v1-теста выше по файлу)."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(16)]  # span [50,145]
    mid_xs = [(475.0 + 5 * i, 495.0 + 5 * i) for i in range(16)]  # span [475,570]
    right_xs = [(855.0 + 5 * i, 875.0 + 5 * i) for i in range(16)]  # span [855,950]
    boxes = full_height_boxes(left_xs + mid_xs + right_xs, FH)
    assert len(boxes) == 48

    seg = segment_boxes(boxes, FW, FH, min_boxes=DEFAULT_MIN_BOXES)  # дефолтные min_boxes И min_text_aspect
    assert seg.text_aspect == pytest.approx(2.25)
    assert seg.is_shelf is True
    assert len(seg.crops) == 3


def test_text_aspect_boundary_is_inclusive_at_exactly_default_threshold():
    """Граница порога (брифа, п. 3в): аспект РОВНО 1.2 -> `is_shelf=True`
    (условие `text_aspect >= min_text_aspect`, `>=` — включительно, тот же
    принцип, что `n_row_boxes >= min_boxes` в `test_exactly_min_boxes_threshold_
    is_inclusive` выше). Ширина охвата 480 (=[300,395]+[685,780], gap=290) /
    row_height 400 = aspect 1.2 ровно."""
    left_xs = [(300.0 + 5 * i, 320.0 + 5 * i) for i in range(16)]  # span [300,395]
    right_xs = [(685.0 + 5 * i, 705.0 + 5 * i) for i in range(16)]  # span [685,780], gap=290
    boxes = full_height_boxes(left_xs + right_xs, FH)

    seg = segment_boxes(boxes, FW, FH, min_boxes=DEFAULT_MIN_BOXES)
    assert seg.text_aspect == pytest.approx(DEFAULT_MIN_TEXT_ASPECT)
    assert seg.is_shelf is True  # РОВНО на пороге -> проходит (>=, включительно)
    assert len(seg.crops) == 2


def test_text_aspect_boundary_just_below_threshold_is_excluded():
    """Тот же боковой сдвиг, что и тест выше, но правая колонка на 4px левее
    (span [681,776] вместо [685,780]) -> ширина охвата 476 / row_height 400 =
    aspect 1.19 < 1.2 -> `is_shelf=False`. Пара с тестом выше документирует
    ОБЕ стороны границы порога буквально соседними значениями аспекта."""
    left_xs = [(300.0 + 5 * i, 320.0 + 5 * i) for i in range(16)]  # span [300,395]
    right_xs = [(681.0 + 5 * i, 701.0 + 5 * i) for i in range(16)]  # span [681,776], gap=286
    boxes = full_height_boxes(left_xs + right_xs, FH)

    seg = segment_boxes(boxes, FW, FH, min_boxes=DEFAULT_MIN_BOXES)
    assert seg.text_aspect == pytest.approx(1.19)
    assert seg.is_shelf is False  # чуть НИЖЕ порога -> не проходит
    assert seg.crops == ((0, 0, FW, FH),)


# --------------------------------------------------------------------------------------
# ShelfDetector / detect_boxes — деградация, без реального rapidocr (test_ocr_rapid.py)
# --------------------------------------------------------------------------------------


class _FakeDetector:
    def __init__(self, boxes: np.ndarray | None = None):
        self._boxes = boxes if boxes is not None else np.zeros((0, 4, 2), dtype=np.float32)
        self.calls = 0

    def detect(self, image_arr: np.ndarray) -> np.ndarray:
        self.calls += 1
        return self._boxes


def test_detect_boxes_uses_injected_detector_not_default():
    fake = _FakeDetector(np.array([box(0, 0, 10, 10)], dtype=np.float32))
    out = detect_boxes(np.zeros((5, 5, 3), dtype=np.uint8), detector=fake)
    assert fake.calls == 1
    assert out.shape == (1, 4, 2)


def test_shelf_detector_degrades_to_empty_when_rapidocr_not_importable(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidocr", None)
    detector = ShelfDetector()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert out.shape == (0, 4, 2)
    assert any("rapidocr" in str(w.message) for w in caught)


def test_shelf_detector_degrades_to_empty_when_construction_fails(monkeypatch):
    import sys
    import types

    fake_module = types.SimpleNamespace(
        RapidOCR=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        OCRVersion=types.SimpleNamespace(PPOCRV5="v5"),
        ModelType=types.SimpleNamespace(MOBILE="mobile"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)
    detector = ShelfDetector()
    with pytest.warns(UserWarning, match="не удалось создать детектор"):
        out = detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert out.shape == (0, 4, 2)


def test_shelf_detector_degrades_to_empty_when_call_fails(monkeypatch):
    import sys
    import types

    class _BoomEngine:
        def __call__(self, arr):
            raise RuntimeError("boom")

    fake_module = types.SimpleNamespace(
        RapidOCR=lambda **kw: _BoomEngine(),
        OCRVersion=types.SimpleNamespace(PPOCRV5="v5"),
        ModelType=types.SimpleNamespace(MOBILE="mobile"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)
    detector = ShelfDetector()
    with pytest.warns(UserWarning, match="сбой детекции"):
        out = detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert out.shape == (0, 4, 2)


def test_shelf_detector_caches_engine_across_calls(monkeypatch):
    import sys
    import types

    construct_calls = []

    class _Engine:
        def __call__(self, arr):
            return types.SimpleNamespace(boxes=None)

    def _construct(**kw):
        construct_calls.append(kw)
        return _Engine()

    fake_module = types.SimpleNamespace(
        RapidOCR=_construct,
        OCRVersion=types.SimpleNamespace(PPOCRV5="v5"),
        ModelType=types.SimpleNamespace(MOBILE="mobile"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)
    detector = ShelfDetector()
    detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert len(construct_calls) == 1


def test_shelf_detector_passes_expected_params(monkeypatch):
    import sys
    import types

    captured = {}

    def _construct(params):
        captured.update(params)
        return lambda arr: types.SimpleNamespace(boxes=None)

    fake_module = types.SimpleNamespace(
        RapidOCR=_construct,
        OCRVersion=types.SimpleNamespace(PPOCRV5="v5"),
        ModelType=types.SimpleNamespace(MOBILE="mobile"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)
    ShelfDetector().detect(np.zeros((10, 10, 3), dtype=np.uint8))
    assert captured["Global.use_rec"] is False
    assert captured["Global.use_cls"] is False
    assert captured["Det.box_thresh"] == 0.3
    assert captured["Det.unclip_ratio"] == 2.0
    assert captured["Det.limit_side_len"] == 1800
    assert captured["Det.limit_type"] == "max"


def test_detect_boxes_result_none_boxes_degrades_to_empty(monkeypatch):
    import sys
    import types

    fake_module = types.SimpleNamespace(
        RapidOCR=lambda **kw: (lambda arr: types.SimpleNamespace(boxes=None)),
        OCRVersion=types.SimpleNamespace(PPOCRV5="v5"),
        ModelType=types.SimpleNamespace(MOBILE="mobile"),
    )
    monkeypatch.setitem(sys.modules, "rapidocr", fake_module)
    out = detect_boxes(np.zeros((10, 10, 3), dtype=np.uint8), detector=ShelfDetector())
    assert out.shape == (0, 4, 2)


# --------------------------------------------------------------------------------------
# segment_shelf — обвязка детектор+геометрия, детектор подменяется
# --------------------------------------------------------------------------------------


def test_segment_shelf_with_fake_detector_no_boxes_is_not_a_shelf():
    fake = _FakeDetector()
    seg = segment_shelf(np.zeros((H, W, 3), dtype=np.uint8), detector=fake)
    assert isinstance(seg, ShelfSegmentation)
    assert seg.is_shelf is False
    assert seg.crops == ((0, 0, W, H),)


def test_segment_shelf_with_fake_detector_shelf_pattern_triggers_gate():
    """`min_text_aspect=0.0` — тест про обвязку детектор+геометрия (v1), тот же
    узкий паттерн (аспект 0.93), что и `test_two_separated_clusters_*` выше."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(16)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(16)]
    boxes = full_height_boxes(left_xs + right_xs, H)
    fake = _FakeDetector(boxes)
    image_arr = np.random.default_rng(2).integers(0, 256, size=(H, W, 3)).astype(np.uint8)
    seg = segment_shelf(image_arr, detector=fake, min_boxes=DEFAULT_MIN_BOXES, min_text_aspect=0.0)
    assert seg.is_shelf is True
    assert len(seg.crops) == 2


def test_segment_shelf_min_boxes_override_is_respected():
    """`min_text_aspect=0.0` на `seg_lowered` — иначе гейт v2 (аспект 0.86 <
    1.2) держал бы `is_shelf=False` даже после снижения `min_boxes`, и тест
    перестал бы проверять именно override `min_boxes`."""
    left_xs = [(50.0 + 5 * i, 70.0 + 5 * i) for i in range(5)]
    right_xs = [(700.0 + 5 * i, 720.0 + 5 * i) for i in range(5)]  # 10 боксов, < 30 по умолчанию
    boxes = full_height_boxes(left_xs + right_xs, H)
    fake = _FakeDetector(boxes)
    image_arr = np.zeros((H, W, 3), dtype=np.uint8)
    seg_default = segment_shelf(image_arr, detector=fake)
    assert seg_default.is_shelf is False
    seg_lowered = segment_shelf(image_arr, detector=fake, min_boxes=10, min_text_aspect=0.0)
    assert seg_lowered.is_shelf is True
