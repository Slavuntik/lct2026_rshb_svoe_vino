"""cv/label_crop.py — область этикетки внутри кропа бутылки, БЕЗ отдельной модели-
детектора (agents/H3-label-crop-ocr.md).

`label_bbox()` — перенос ДОСЛОВНО (та же арифметика/константы, тот же алгоритм) из
прототипа-исследования `qa/real_photos_label_crops.py` (оркестратор, 22.09,
`reports/label-crop-study.md`). Идея: детектор текста RapidOCR УЖЕ отработал по кропу
бутылки на одном из масштабов слияния (`cv.ocr_rapid`, ~0.2 с, лишней детекции не
требуется) — боксы этого прохода кластеризуются в область этикетки: центральная
колонка кропа, кластер по вертикальной близости начиная с крупного бокса ближе к
центру, плюс поля, плюс минимальный размер (этикетка часто отдаёт детектору только
шапку с винодельней — расширяем ВНИЗ, туда, где на реальных этикетках название и
сахар, см. `reports/label-crop-study.md`, "Как вырезали этикетку без новой модели").

Найдено на 100/100 живых фото каталога (медиана 16% кадра) и 1985/2058 эталонах
каталога (см. отчёт выше) — но покрытие НЕ 100%: `label_bbox()` возвращает `None`,
когда ни один бокс не проходит фильтр скора/центра/высоты (кадр без читаемого текста
в центральной колонке, либо детектор вообще ничего не нашёл). Скрипт-исследование
(`qa/real_photos_label_crops.py::main`) в этом случае подставлял ВЕСЬ кроп бутылки как
собственный фолбэк (`found=False`, нужно было для 100%-покрытия визуальных
контакт-листов) — этот модуль такого фолбэка НЕ содержит и не обязан: вызывающий код
третьего OCR-прохода (`cv.ocr_rapid.RapidOcrReader`) трактует `None` как "пропустить
проход" (третий проход по ВСЕМУ кропу бутылки на новом масштабе измерен отдельно и
пользы не даёт, см. `reports/label-crop-study.md`, "при 1600 px — 0") — то есть тот же
`None`, разные, осознанно НЕ единые решения на стороне двух разных вызывающих кодов.
"""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

# Константы прототипа — см. qa/real_photos_label_crops.py::label_bbox (значения не
# менялись при переносе, "дословно" из брифа H3 задача 1).
_MIN_SCORE = 0.5
_CENTER_X_MIN_FRAC = 0.18
_CENTER_X_MAX_FRAC = 0.82
_MIN_BOX_HEIGHT_FRAC = 0.008
_SEED_CENTER_Y_FRAC = 0.58  # этикетка обычно в нижней половине кропа бутылки
_SEED_DIST_WEIGHT = 1.5
_VERTICAL_GAP_MULT = 6.0  # текст на этикетке разрежен: шапка — пробел — название
_HORIZONTAL_SLACK_FRAC = 0.1
_MARGIN_WIDTH_REL = 0.18
_MARGIN_WIDTH_ABS_FRAC = 0.02
_MARGIN_HEIGHT_REL = 0.15
_MARGIN_HEIGHT_ABS_FRAC = 0.02
_MIN_LABEL_WIDTH_FRAC = 0.45
_MIN_LABEL_HEIGHT_FRAC = 0.32
_GROWTH_UP_FRAC = 0.3  # рост к min_h — ВНИЗ смещённый (шапка с винодельней вверху)
_GROWTH_DOWN_FRAC = 0.7


def label_bbox(
    boxes: Sequence[Any], scores: Sequence[float], w: float, h: float
) -> tuple[float, float, float, float] | None:
    """Прямоугольник этикетки в координатах кропа (w×h) или `None`.

    `boxes` — по одному 4-точечному (или Nx2) полигону на бокс детектора текста (как
    отдаёт `rapidocr` — `RapidOCROutput.boxes`, см. `cv.ocr_rapid`); только
    min/max по координатам используются, порядок точек внутри бокса не важен.
    `scores` — confidence детектора на той же позиции, что и `boxes` (короче/длиннее
    — лишние элементы одной из последовательностей молча отбрасываются `zip()`,
    та же дисциплина, что `cv.ocr_rapid.RapidOcrReader._extract_text()`).

    `None` — законный исход (не ошибка): ни один бокс не остался после фильтра
    скора/центра/высоты (см. `_MIN_SCORE`/`_CENTER_X_MIN_FRAC`.../`_MIN_BOX_HEIGHT_
    FRAC` выше) — например, пустые `boxes`, детектор не нашёл текст в центральной
    колонке кропа, или все найденные строки не набрали минимальный порог высоты.
    Вызывающий код решает сам, что делать с `None` (см. докстринг модуля).
    """
    cand = []
    for b, s in zip(boxes, scores):
        b = np.asarray(b, dtype=np.float32)
        x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()
        cx = (x0 + x1) / 2
        if (
            s < _MIN_SCORE
            or not (_CENTER_X_MIN_FRAC * w <= cx <= _CENTER_X_MAX_FRAC * w)
            or (y1 - y0) < _MIN_BOX_HEIGHT_FRAC * h
        ):
            continue
        cand.append([x0, y0, x1, y1])
    if not cand:
        return None

    cand = np.array(cand)
    centers = np.stack([(cand[:, 0] + cand[:, 2]) / 2, (cand[:, 1] + cand[:, 3]) / 2], axis=1)
    # старт — крупный бокс ближе к центру кадра (этикетка обычно в нижней половине бутылки)
    area = (cand[:, 2] - cand[:, 0]) * (cand[:, 3] - cand[:, 1])
    dist = np.hypot((centers[:, 0] - 0.5 * w) / w, (centers[:, 1] - _SEED_CENTER_Y_FRAC * h) / h)
    seed = int(np.argmax(area / area.max() - _SEED_DIST_WEIGHT * dist))
    inside = {seed}
    med_h = float(np.median(cand[:, 3] - cand[:, 1]))
    changed = True
    while changed:
        changed = False
        bx0 = cand[list(inside), 0].min()
        by0 = cand[list(inside), 1].min()
        bx1 = cand[list(inside), 2].max()
        by1 = cand[list(inside), 3].max()
        for i in range(len(cand)):
            if i in inside:
                continue
            gap_y = max(cand[i, 1] - by1, by0 - cand[i, 3], 0)
            overlap_x = min(cand[i, 2], bx1 + _HORIZONTAL_SLACK_FRAC * w) - max(
                cand[i, 0], bx0 - _HORIZONTAL_SLACK_FRAC * w
            )
            if gap_y <= _VERTICAL_GAP_MULT * med_h and overlap_x > 0:
                inside.add(i)
                changed = True

    idx = list(inside)
    x0, y0, x1, y1 = cand[idx, 0].min(), cand[idx, 1].min(), cand[idx, 2].max(), cand[idx, 3].max()
    mx = _MARGIN_WIDTH_REL * (x1 - x0) + _MARGIN_WIDTH_ABS_FRAC * w
    my = _MARGIN_HEIGHT_REL * (y1 - y0) + _MARGIN_HEIGHT_ABS_FRAC * h
    x0, y0, x1, y1 = x0 - mx, y0 - my, x1 + mx, y1 + my

    # минимальный размер области: этикетка не уже ~45% кропа бутылки и не ниже ~32% его высоты;
    # добираем симметрично по ширине и со смещением ВНИЗ по высоте (шапка с винодельней — вверху этикетки)
    min_w, min_h = _MIN_LABEL_WIDTH_FRAC * w, _MIN_LABEL_HEIGHT_FRAC * h
    if x1 - x0 < min_w:
        c = (x0 + x1) / 2
        x0, x1 = c - min_w / 2, c + min_w / 2
    if y1 - y0 < min_h:
        add = min_h - (y1 - y0)
        y0, y1 = y0 - _GROWTH_UP_FRAC * add, y1 + _GROWTH_DOWN_FRAC * add

    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)
