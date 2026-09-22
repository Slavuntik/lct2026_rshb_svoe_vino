"""cv/shelf_crop.py — сегментация кадра ЦЕЛОЙ ПОЛКИ на бутылки, кроп центральной
(шаг 0 конвейера, ДО `ImageIndex.search()`/OCR — agents/ML-2-shelf-crop.md).

## Основание (ml-lead, 22.09, reports/ml-lead-shelf-crop.md; полный разбор — там же)

Полевые фото ЦЕЛЫХ ПОЛОК (`Field/`) в боевой конфигурации дают top-1 1/8 (qa-auto,
reports/qa-auto-field-photos.md) — причина: боевой центральный кроп кадра (доли
0.15-0.85 ширины) на кадре полки содержит 3-4+ бутылки вместо одной, OCR/CV
получают мешанину текста и проигрывают. Идея: боксы детектора текста (RapidOCR,
детекция БЕЗ распознавания — `Global.use_rec: False`, отдельный дешёвый проход
~0.2-0.6с) = грубая проекция бутылок на кадре.

Перенесено ДОСЛОВНО (та же арифметика, те же пороги/константы) из прототипа-
исследования `qa/real_photos_shelf_crop.py` (ml-lead, коммит 9ed4c66) —
`segment_shelf()`/`expand_from_center()`/`run_clusters()`/`geometric_columns()`/
`voronoi_bounds()`. Единственная адаптация: прототип работал с `PIL.Image`
(исследовательский скрипт), эта версия — с RGB uint8 ndarray (H,W,3), как весь
остальной боевой пакет (`cv.imageio.decode_image`, `cv.label_crop`, `cv.ocr_rapid`) —
нулей по арифметике это не меняет, только форму входа/константы W/H переставлены
местами (numpy — (H,W), PIL.Image.size — (W,H)).

## Алгоритм (см. reports/ml-eng-ml2.md, "Перенос" — детали свода к боевому виду)

1. РЯД (полка): растим окно от Y-центра кадра наружу по сглаженной (`smooth_frac`
   высоты) плотности текстовых боксов до структурного разрыва между полками
   (`expand_from_center`) — надёжнее жёсткого разбиения на N рядов порогом (гоняет
   бюджет на шум ценников/этикетки внутри одного ряда).
2. КОЛОНКИ (бутылки) внутри ряда: реальные разрывы НУЛЕВОГО X-покрытия
   (`run_clusters`, строже мягкого порога плотности — не режет ряд там, где текст
   просто РЕЖЕ, только там, где боксов нет вовсе). Меньше 2 колонок — геометрический
   фолбэк без модели: впадины вертикальной проекции границ Sobel-X (`geometric_columns`).
3. Раздел Вороного между соседними колонками (`voronoi_bounds`, БЕЗ нахлёста —
   граница ровно на полпути между центрами) = кроп «одна бутылка с полями» (поля —
   естественное следствие раздела на полпути к соседу, не отдельный отступ);
   ближайшая к X-центру кадра колонка = «центральная» (`center_index`).
4. **Гейт «это вообще полка»** (обязателен — без него ложно режутся студийные фото
   каталога, см. отчёт, раздел "Гейт"): колонок >= 2 И боксов в полосе ряда >=
   `min_boxes` (порог 30 подобран ml-lead на ВСЕХ 100 фото каталога: максимум боксов
   у НЕ-полочных — 51, целевые полевые ряды — 41-118, пересечения при 30 нет; без
   гейта — катастрофа на каталоге, 95.2%→51.6%). Гейт не пройден -> `crops` — РОВНО
   весь кадр, `is_shelf=False`; вызывающий код (`apps/api/app/cv/service.py`) обязан
   в этом случае передать исходные байты дальше ПОБИТОВО, без единого перекодирования
   (регрессия на не-полочных фото структурно невозможна).

`segment_boxes()` — чистая геометрия (боксы уже даны, никакой модели) — основная
цель юнит-тестов брифа (синтетические паттерны боксов). `detect_boxes()`/
`segment_shelf()` — обвязка вокруг RapidOCR (лениво импортируется, деградирует на
сбое импорта/конструктора/вызова в warning + пустой список боксов — та же
дисциплина, что `cv.ocr_rapid.RapidOcrReader`, см. `test_ocr_rapid.py`); их тесты
подменяют детектор напрямую, реальный rapidocr в юнитах не запускается.

## Соседние колонки при пограничном центре (ml-lead, риски отчёта, п.2 — опционально)

`ShelfSegmentation.candidate_indices` — обычно `(center_index,)`; когда РАЗНИЦА
расстояний до X-центра кадра между самой близкой и следующей по близости колонкой
меньше `border_tie_frac` доли ширины кадра, добавляет и вторую (соседнюю) колонку —
дешёвая, чисто геометрическая находка (без доп. детекции/OCR), которую вызывающий
код МОЖЕТ использовать, чтобы попробовать соседний кроп при неоднозначной границе
(F04/F30 в отчёте ml-lead — промах ровно на границе раздела Вороного). Флаг
`CV_SHELF_CHECK_NEIGHBORS` (apps/api) решает, использовать ли это поле вообще —
сама сегментация считает его безусловно (наносекунды на кадр, чистая арифметика).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

Box = tuple[int, int, int, int]  # x0, y0, x1, y1 в пикселях исходного кадра, левый верх/правый низ

DEFAULT_MIN_BOXES = 30  # см. докстринг модуля, "Гейт"
DEFAULT_SEARCH_FRAC = 0.4  # expand_from_center: доля кадра по каждую сторону от центра
DEFAULT_SMOOTH_FRAC = 0.045  # expand_from_center: ширина сглаживающего окна (доля bins)
DEFAULT_ROW_BINS = 500
DEFAULT_COL_BINS = 600
DEFAULT_COL_MIN_GAP_FRAC = 0.02
DEFAULT_GEOM_MAX_BOTTLES = 8
DEFAULT_GEOM_MIN_BOTTLE_FRAC = 0.08
DEFAULT_BORDER_TIE_FRAC = 0.06  # candidate_indices: "почти на границе" — доля ширины кадра

# qa-manual, 22.09 (reports/qa-manual-field-bottles.md): 25/85 кропов нарезки — брак
# сегментации (>= 2 бутылки в одном кропе, до 10+), ЦЕЛИКОМ из-за `col_method=geometric`
# на F02/F10 — плотные ряды ОДИНАКОВЫХ бутылок без читаемого текста между ними, где Sobel-
# проекция не находит разрывов и оставляет 2-3 ШИРОКИХ "колонки" вместо 8-10 узких (риск №1
# ml-lead, здесь подтверждён на реальных данных: F10 — сегмент 1829px при росте ряда 1416px).
# Правка тимлида (22.09, после находки): бутылка стоя — примерно 0.25-0.4 высоты СВОЕГО РЯДА
# по ширине; сегмент геометрического фолбэка ШИРЕ верхней границы -> считаем, что внутри него
# несколько бутылок подряд, и режем на РАВНЫЕ части по "ожидаемой" ширине (не пытаемся найти
# индивидуальные границы — Sobel уже показал, что их не видно; равные части — тот же
# принцип "честной деградации", что и остальной модуль: не точнее, чем позволяют данные).
DEFAULT_MAX_BOTTLE_WIDTH_FRAC = 0.4  # верхняя граница диапазона — сегмент шире не может быть одной бутылкой
DEFAULT_EXPECTED_BOTTLE_WIDTH_FRAC = 0.32  # середина диапазона 0.25-0.4 — используется для расчёта числа частей

# Детектор — отдельный, самый дешёвый проход RapidOCR (см. докстринг модуля,
# "Основание"): детекция БЕЗ распознавания. Пороги — ml-lead, 22.09 (те же значения,
# что hack-v9 использует для ЧТЕНИЯ текста, cv.ocr_rapid.DEFAULT_DET_BOX_THRESH/
# DEFAULT_DET_UNCLIP — совпадение констант случайно: подобраны независимо, для
# разных целей, здесь НЕ читаются из CV_OCR_DET_*, чтобы эволюция порогов чтения
# текста не тихо не задевала геометрию гейта).
DEFAULT_DET_BOX_THRESH = 0.3
DEFAULT_DET_UNCLIP = 2.0
DEFAULT_DET_LIMIT_SIDE_LEN = 1800


@dataclass(frozen=True)
class ShelfSegmentation:
    """Результат `segment_boxes()`/`segment_shelf()`.

    `crops` — слева направо, в пикселях исходного кадра; гейт не пройден (или
    боксов нет вовсе) -> ровно один элемент, весь кадр, побитово. `center_index` —
    индекс «центральной» (ближайшей к X-центру кадра) колонки в `crops`.
    `col_method` — "ocr" (реальные разрывы X-покрытия), "geometric" (Sobel-фолбэк)
    или "none" (колонок < 2 даже после фолбэка/боксов нет). `n_row_boxes` — боксов
    внутри выбранного ряда (вход гейта); `n_boxes` — боксов на всём кадре (диагностика).
    """

    crops: tuple[Box, ...]
    center_index: int
    is_shelf: bool
    n_row_boxes: int
    col_method: str
    n_boxes: int
    candidate_indices: tuple[int, ...] = (0,)

    @property
    def central_crop(self) -> Box:
        return self.crops[self.center_index]


# ---------------------------------------------------------------------------------
# Чистая геометрия — ДОСЛОВНЫЙ перенос qa/real_photos_shelf_crop.py (арифметика и
# константы по умолчанию не менялись). Без модели, без диска/сети — тестируется
# синтетическими боксами (test_shelf_crop.py), тот же принцип, что cv.label_crop.
# ---------------------------------------------------------------------------------


def intervals_xy(boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(N,4,2) полигонов -> (x0, y0, x1, y1), по одному значению на бокс (min/max
    по координатам — порядок точек внутри полигона не важен, как в `cv.label_crop`)."""
    x0 = boxes[:, :, 0].min(axis=1)
    x1 = boxes[:, :, 0].max(axis=1)
    y0 = boxes[:, :, 1].min(axis=1)
    y1 = boxes[:, :, 1].max(axis=1)
    return x0, y0, x1, y1


def expand_from_center(
    a0: np.ndarray, a1: np.ndarray, frame_size: float,
    search_frac: float = DEFAULT_SEARCH_FRAC, bins: int = DEFAULT_ROW_BINS,
    smooth_frac: float = DEFAULT_SMOOTH_FRAC,
) -> tuple[float, float]:
    """Граница «своего» ряда вокруг геометрического центра оси: широко сглаженный
    профиль плотности боксов -> самая слабая точка (минимум) по каждую сторону от
    центра в пределах `search_frac*frame_size`. Широкое сглаживание гасит мелкие
    внутристрочные прогалы (этикетка/ценник) и оставляет структурные разрывы между
    полками (reports/ml-lead-shelf-crop.md)."""
    n = len(a0)
    bin_w = frame_size / bins
    cov = np.zeros(bins, dtype=np.float32)
    for i in range(n):
        i0 = max(0, int(a0[i] / bin_w))
        i1 = min(bins, int(np.ceil(a1[i] / bin_w)))
        if i1 > i0:
            cov[i0:i1] += 1
    k = max(3, int(smooth_frac * bins))
    smooth = np.convolve(cov, np.ones(k, dtype=np.float32) / k, mode="same")
    c = int(np.clip(0.5 * bins, 0, bins - 1))
    win = max(1, int(search_frac * bins))
    top_lo = max(0, c - win)
    top = top_lo + int(np.argmin(smooth[top_lo:c])) if c > top_lo else 0
    bot_hi = min(bins, c + win)
    bot = c + int(np.argmin(smooth[c:bot_hi])) if bot_hi > c else bins
    return top * bin_w, bot * bin_w


def run_clusters(
    a0: np.ndarray, a1: np.ndarray, frame_size: float,
    min_gap_frac: float = DEFAULT_COL_MIN_GAP_FRAC, bins: int = DEFAULT_COL_BINS,
) -> list[dict]:
    """Кластеры по реальным разрывам НУЛЕВОГО покрытия (строже мягкого порога — не
    режет ряд там, где текст просто РЕЖЕ, только там, где боксов нет вовсе).
    -> список `{"a0":.., "a1":.., "center":..}` слева направо, в пикселях."""
    n = len(a0)
    if n == 0:
        return []
    bin_w = frame_size / bins
    cov = np.zeros(bins, dtype=np.float32)
    for i in range(n):
        i0 = max(0, int(a0[i] / bin_w))
        i1 = min(bins, int(np.ceil(a1[i] / bin_w)))
        if i1 > i0:
            cov[i0:i1] += 1
    covered = cov > 0
    runs, i = [], 0
    while i < bins:
        if covered[i]:
            j = i
            while j < bins and covered[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    if not runs:
        return []
    min_gap_bins = max(1, int(min_gap_frac * bins))
    merged = [runs[0]]
    for r in runs[1:]:
        if r[0] - merged[-1][1] <= min_gap_bins:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [{"a0": i0 * bin_w, "a1": i1 * bin_w, "center": (i0 + i1) / 2 * bin_w} for i0, i1 in merged]


def _split_oversized_segments(
    segs: list[tuple[float, float]], row_h: float,
    max_frac: float = DEFAULT_MAX_BOTTLE_WIDTH_FRAC, expected_frac: float = DEFAULT_EXPECTED_BOTTLE_WIDTH_FRAC,
) -> list[tuple[float, float]]:
    """qa-manual, reports/qa-manual-field-bottles.md: сегмент `geometric_columns()`
    ШИРЕ `max_frac*row_h` не может быть одной бутылкой (одна бутылка стоя — примерно
    `expected_frac*row_h` по ширине) — режем на РАВНЫЕ части (Sobel уже показал, что
    индивидуальных границ внутри не видно, точнее — не из чего). Число частей —
    ближайшее целое к `width/(expected_frac*row_h)`, но не меньше 2 (сегмент ведь
    превысил верхнюю границу одной бутылки). `row_h <= 0` (вырожденный случай) ->
    без изменений, деление на ноль не возникает."""
    if row_h <= 0:
        return segs
    max_w = max_frac * row_h
    expected_w = expected_frac * row_h
    if expected_w <= 0:
        return segs
    out: list[tuple[float, float]] = []
    for a, b in segs:
        width = b - a
        if width <= max_w:
            out.append((a, b))
            continue
        n_sub = max(2, round(width / expected_w))
        step = width / n_sub
        out.extend((a + i * step, a + (i + 1) * step) for i in range(n_sub))
    return out


def geometric_columns(
    gray: np.ndarray, y0: float, y1: float, w: float,
    max_bottles: int = DEFAULT_GEOM_MAX_BOTTLES, min_bottle_frac: float = DEFAULT_GEOM_MIN_BOTTLE_FRAC,
    max_bottle_width_frac: float = DEFAULT_MAX_BOTTLE_WIDTH_FRAC,
    expected_bottle_width_frac: float = DEFAULT_EXPECTED_BOTTLE_WIDTH_FRAC,
) -> list[tuple[float, float]]:
    """Фолбэк без модели: впадины вертикальной проекции границ (Sobel-X) = промежутки
    между бутылками, когда текстовых боксов для кластеризации мало/шумно. `gray` —
    полный кадр в оттенках серого (HxW); `y0`/`y1` — ряд (пиксели). -> список
    `(a, b)` отрезков слева направо либо `[]`, если сегментов < 2.

    qa-manual (reports/qa-manual-field-bottles.md): плотный ряд ОДИНАКОВЫХ бутылок
    без текста между ними даёт Sobel-впадины только на КРАЯХ группы, не между
    отдельными бутылками — без правки сегмент охватывал бы 10+ бутылок разом
    (F02/F10 на реальных полевых фото). Каждый итоговый сегмент шире
    `max_bottle_width_frac` высоты ряда — режется на равные части
    (`_split_oversized_segments`), см. её докстринг."""
    band = gray[int(y0):int(y1), :].astype(np.float32)
    if band.shape[0] < 4:
        return []
    energy = np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
    k = max(3, int(w) // 150)
    smooth = np.convolve(energy, np.ones(k, dtype=np.float32) / k, mode="same")
    thresh = np.percentile(smooth, 35)
    min_gap_px = max(4, int(0.015 * w))
    min_bottle_px = int(min_bottle_frac * w)
    cand = sorted((smooth[i], i) for i in range(1, int(w) - 1) if smooth[i] <= thresh)
    chosen: list[int] = []
    for _, i in cand:
        if all(abs(i - c) >= min_gap_px for c in chosen):
            chosen.append(i)
        if len(chosen) >= max_bottles - 1:
            break
    chosen.sort()
    bounds = [0] + chosen + [int(w)]
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    segs = [s for s in segs if s[1] - s[0] >= min_bottle_px]
    if len(segs) < 2:
        return []
    return _split_oversized_segments(segs, y1 - y0, max_bottle_width_frac, expected_bottle_width_frac)


def voronoi_bounds(centers: list[float], lo: float, hi: float) -> list[tuple[float, float]]:
    """Границы на полпути между соседними центрами — БЕЗ нахлёста (см. докстринг
    модуля, "Соседние колонки" — здесь и рождается off-by-one на пограничном
    кадре: F04/F30 в reports/ml-lead-shelf-crop.md)."""
    b = [lo] + [(c1 + c2) / 2 for c1, c2 in zip(centers, centers[1:])] + [hi]
    return list(zip(b[:-1], b[1:]))


def _candidate_indices(centers: list[float], frame_w: float, border_tie_frac: float) -> tuple[int, ...]:
    """agents/ML-2-shelf-crop.md, доп. пункт (риски отчёта ml-lead, п.2): помимо
    `center_index`, отмечает единственного НЕПОСРЕДСТВЕННОГО соседа по индексу
    колонок, когда тот почти так же близок к X-центру кадра (разница расстояний
    <= `border_tie_frac*frame_w`) — дешёвая геометрическая находка, независимая от
    того, воспользуется ли ей вызывающий код (`CV_SHELF_CHECK_NEIGHBORS`)."""
    dist = [abs(c - 0.5 * frame_w) for c in centers]
    order = sorted(range(len(centers)), key=lambda i: dist[i])
    best = order[0]
    candidates = [best]
    if len(order) > 1:
        runner_up = order[1]
        if abs(runner_up - best) == 1 and (dist[runner_up] - dist[best]) <= border_tie_frac * frame_w:
            candidates.append(runner_up)
    return tuple(sorted(candidates))


def segment_boxes(
    boxes: np.ndarray,
    frame_w: int,
    frame_h: int,
    *,
    gray: np.ndarray | None = None,
    gate: bool = True,
    min_boxes: int = DEFAULT_MIN_BOXES,
    search_frac: float = DEFAULT_SEARCH_FRAC,
    smooth_frac: float = DEFAULT_SMOOTH_FRAC,
    border_tie_frac: float = DEFAULT_BORDER_TIE_FRAC,
) -> ShelfSegmentation:
    """Чистая геометрия сегментации — БЕЗ модели, детерминирована, боксы уже даны
    (см. `detect_boxes()`/`segment_shelf()` ниже для боевого пути с детектором).
    `boxes` — (N,4,2) полигонов, как отдаёт `rapidocr`; N=0 -> законный «не полка»
    исход, `is_shelf=False`, `crops` — ровно весь кадр (см. докстринг модуля,
    "Алгоритм", п.4). `gray` — кадр в оттенках серого для геометрического
    фолбэка колонок; `None` -> фолбэк недоступен (тот же исход, что фолбэк не
    нашёл >= 2 сегментов — `col_method="none"`).
    """
    if len(boxes) == 0:
        return ShelfSegmentation(
            crops=((0, 0, frame_w, frame_h),), center_index=0, is_shelf=False,
            n_row_boxes=0, col_method="none", n_boxes=0,
        )
    x0, y0, x1, y1 = intervals_xy(boxes)
    ry0, ry1 = expand_from_center(y0, y1, frame_h, search_frac=search_frac, smooth_frac=smooth_frac)
    row_mask = (y1 > ry0) & (y0 < ry1)
    n_row_boxes = int(row_mask.sum())
    rx0, rx1 = x0[row_mask], x1[row_mask]

    cols = run_clusters(rx0, rx1, frame_w)
    col_method = "ocr"
    if len(cols) < 2:
        segs = geometric_columns(gray, ry0, ry1, frame_w) if gray is not None else []
        if segs:
            cols = [{"center": (a + b) / 2} for a, b in segs]
            col_method = "geometric"
        else:
            col_method = "none"

    is_shelf = len(cols) >= 2 and n_row_boxes >= min_boxes
    if gate and not is_shelf:
        return ShelfSegmentation(
            crops=((0, 0, frame_w, frame_h),), center_index=0, is_shelf=False,
            n_row_boxes=n_row_boxes, col_method=col_method, n_boxes=len(boxes),
        )

    if len(cols) < 2:
        crops: tuple[Box, ...] = ((0, int(ry0), frame_w, int(ry1)),)
        center_index = 0
        candidate_indices: tuple[int, ...] = (0,)
    else:
        centers = sorted(c["center"] for c in cols)
        bounds = voronoi_bounds(centers, 0, frame_w)
        crops = tuple((int(a), int(ry0), int(b), int(ry1)) for a, b in bounds)
        center_index = int(np.argmin([abs(c - 0.5 * frame_w) for c in centers]))
        candidate_indices = _candidate_indices(centers, frame_w, border_tie_frac)

    return ShelfSegmentation(
        crops=crops, center_index=center_index, is_shelf=is_shelf,
        n_row_boxes=n_row_boxes, col_method=col_method, n_boxes=len(boxes),
        candidate_indices=candidate_indices,
    )


# ---------------------------------------------------------------------------------
# Детектор — обвязка вокруг RapidOCR (агент ML-2, по образцу cv.ocr_rapid.
# RapidOcrReader._engine_for(): ленивый импорт, деградация в warning + пустой
# результат на сбое импорта/конструктора/вызова, не исключение/500). Тесты
# подменяют `detector=` НАПРЯМУЮ (фейковый объект с `.detect()`) либо
# `sys.modules["rapidocr"]` для веток деградации — реальный rapidocr в юнитах
# не запускается (та же дисциплина, что test_ocr_rapid.py).
# ---------------------------------------------------------------------------------


class ShelfDetector:
    """Детектор текста БЕЗ распознавания (`Global.use_rec: False`) — самый дешёвый
    проход RapidOCR (~0.2-0.6с), только для геометрии (гейт/ряд/колонки), не для
    чтения текста (см. докстринг модуля, "Основание"). Один экземпляр на процесс
    (см. `_DEFAULT_DETECTOR` ниже), лениво создаёт и кэширует движок."""

    def __init__(self) -> None:
        self._engine: Any | None = None
        self._unavailable = False

    def _engine_or_none(self) -> Any | None:
        if self._engine is not None:
            return self._engine
        if self._unavailable:
            return None
        try:
            from rapidocr import ModelType, OCRVersion, RapidOCR
        except Exception as exc:  # noqa: BLE001 — деградация: пакет не установлен/сломан
            warnings.warn(
                f"cv.shelf_crop: импорт rapidocr не удался ({exc!r}) — гейт «полка» "
                "недоступен на этом кадре, шаг сегментации пропускается",
                stacklevel=2,
            )
            self._unavailable = True
            return None
        try:
            engine = RapidOCR(params={
                "Global.use_cls": False, "Global.use_rec": False,
                "Det.limit_side_len": DEFAULT_DET_LIMIT_SIDE_LEN, "Det.limit_type": "max",
                "Det.box_thresh": DEFAULT_DET_BOX_THRESH, "Det.unclip_ratio": DEFAULT_DET_UNCLIP,
                "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
            })
        except Exception as exc:  # noqa: BLE001 — деградация: сеть/диск недоступны при первой загрузке модели
            warnings.warn(
                f"cv.shelf_crop: не удалось создать детектор RapidOCR ({exc!r}) — гейт "
                "«полка» недоступен на этом кадре, шаг сегментации пропускается",
                stacklevel=2,
            )
            self._unavailable = True
            return None
        self._engine = engine
        return engine

    def detect(self, image_arr: np.ndarray) -> np.ndarray:
        """RGB ndarray -> (N,4,2) float32 боксов, либо (0,4,2), если текста нет,
        детектор недоступен (см. выше) или вызов сбоил (warning, не исключение)."""
        engine = self._engine_or_none()
        if engine is None:
            return np.zeros((0, 4, 2), dtype=np.float32)
        try:
            result = engine(np.ascontiguousarray(image_arr))
        except Exception as exc:  # noqa: BLE001 — сбой ВЫЗОВА на конкретном кадре = деградация, не 500
            warnings.warn(f"cv.shelf_crop: сбой детекции ({exc!r}) — боксов 0 на этом кадре", stacklevel=2)
            return np.zeros((0, 4, 2), dtype=np.float32)
        if result is None or result.boxes is None or not len(result.boxes):
            return np.zeros((0, 4, 2), dtype=np.float32)
        return np.asarray(result.boxes, dtype=np.float32)


_DEFAULT_DETECTOR = ShelfDetector()  # общий на процесс, тот же принцип, что cv.verify._OCR/cv.ocr_rapid


def detect_boxes(image_arr: np.ndarray, *, detector: ShelfDetector | None = None) -> np.ndarray:
    """RGB ndarray -> (N,4,2) боксов детектора текста (см. `ShelfDetector.detect`).
    `detector=None` -> общий на процесс `_DEFAULT_DETECTOR` (боевой путь);
    тесты внедряют свой объект — реальный rapidocr не запускается."""
    return (detector or _DEFAULT_DETECTOR).detect(image_arr)


def segment_shelf(
    image_arr: np.ndarray,
    *,
    gate: bool = True,
    min_boxes: int = DEFAULT_MIN_BOXES,
    detector: ShelfDetector | None = None,
) -> ShelfSegmentation:
    """Точка входа для боевого кода (`apps/api/app/cv/service.py`): детекция +
    геометрия за один вызов. `image_arr` — RGB uint8 ndarray, уже декодированный
    (`cv.imageio.decode_image` — EXIF учтён там). `detector` — только для тестов
    (внедрить фейковый объект с методом `.detect()`, см. `test_shelf_crop.py`);
    боевой путь всегда использует общий `_DEFAULT_DETECTOR` (детектор без
    распознавания недоступен/сбоит -> `detect_boxes` отдаёт 0 боксов -> честный
    `is_shelf=False`, не исключение — та же деградация, что весь остальной OCR
    контур пакета)."""
    h, w = image_arr.shape[:2]
    boxes = detect_boxes(image_arr, detector=detector)
    gray = cv2.cvtColor(image_arr, cv2.COLOR_RGB2GRAY) if len(boxes) else None
    return segment_boxes(boxes, w, h, gray=gray, gate=gate, min_boxes=min_boxes)
