"""Нормализация запроса перед эмбеддингом (cv/normalize.py, бриф п.3).

Контракт: `ImageIndex.search()` прогоняет её ДО embed() по умолчанию; отключение —
только явным флагом (A/B в eval): детект области этикетки → кроп → обратная
развёртка цилиндра → выравнивание освещения.

Детектор — классический CV (контуры/градиенты), БЕЗ обучаемой модели (v1 по брифу).
Реальные фото кейса — "у полки, под углом, блики, плохой свет" — так что детектор
обязан вернуть ЧТО-ТО разумное всегда (мягкий fallback на центральный кроп), отказ
на границе нормализации перекладывал бы проблему дальше по пайплайну без всякой
пользы (ни один нижестоящий слой не умеет её решить лучше).

`detect_label_region()` используется также аугментатором (`cv/augment.py`) — чтобы
на цилиндр наклеивалась именно ЭТИКЕТКА эталона (как буквально написано в брифе),
а не весь кадр бутылочного фото целиком (пустое стекло/горлышко над этикеткой).
"""
from __future__ import annotations

import cv2
import numpy as np

from cv import imageio

NORM_SIZE_DEFAULT = 448


def _fallback_region(w: int, h: int) -> np.ndarray:
    mx, my_top, my_bot = w * 0.15, h * 0.32, h * 0.06
    return np.array([[mx, my_top], [w - mx, my_top], [w - mx, h - my_bot], [mx, h - my_bot]], dtype=np.float32)


def _foreground_bbox(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) наибольшей связной области, контрастной фону, оценённому по
    рамке кадра — грубый силуэт "бутылка на фоне", НЕ этикетка. `None`, если фон
    неоднородный (рамка кадра сама пёстрая — типично для тесно заставленной полки,
    или для одной из синтетических "shelf"-подложек аугментатора с полосами) и оценка
    ненадёжна, или связной области разумного размера не нашлось.

    Медиана + MAD, не mean/std: устойчивы, даже если ДО ПОЛОВИНЫ периметра рамки
    случайно легло на другую полосу фона (типично для полосатых "shelf"-подложек) —
    обычный mean/std в этом случае давал уверенно неверную оценку фона вместо
    честного отказа (см. reports/g-report.md, "Предположения")."""
    h, w = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
    m = max(2, int(min(h, w) * 0.03))
    border = np.concatenate([gray[:m, :].ravel(), gray[-m:, :].ravel(), gray[:, :m].ravel(), gray[:, -m:].ravel()])
    bg_med = float(np.median(border))
    mad = float(np.median(np.abs(border - bg_med))) * 1.4826  # -> оценка std для нормального шума
    if mad > 30:  # рамка кадра сама неоднородна — оценка фона ненадёжна
        return None

    diff = np.abs(gray - bg_med)
    mask = (diff > max(20.0, 2.5 * mad)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((max(3, m), max(3, m)), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    if area < 0.05 * h * w or area > 0.97 * h * w:
        return None
    return cv2.boundingRect(cnt)


def detect_label_region(image: np.ndarray) -> np.ndarray:
    """(4,2) float32 — углы предполагаемой области этикетки в пикселях `image`,
    упорядочены [top-left, top-right, bottom-right, bottom-left].

    Стратегия — структурная, не точечно-текстурная (см. reports/g-report.md,
    "Предположения" про две отброшенные версии на Canny-контурах/saliency-карте:
    обе были хрупкими — маленький блик или блестящий капсюль легко набирали более
    высокий локальный "текстурный" скор, чем сама этикетка, и уводили кроп в
    мусорный угол кадра или в область горлышка):
    1. Находим грубый силуэт бутылки целиком — связная область, контрастная фону,
       оценённому по рамке кадра (`_foreground_bbox`). Это НАМНОГО надёжнее, чем
       различать "этикетка vs капсюль vs блик" по текстуре — силуэт против плоского
       фона почти всегда однозначен на студийных фото каталога.
    2. Этикетка занимает НИЖНИЕ ~2/3 силуэта бутылки (капсюль/горлышко — всегда
       сверху; голое основание — тонкая полоса снизу) — простой структурный факт
       о том, как фотографируют вино, устойчивый и не требующий текстурных
       эвристик, которые ломались на глянцевых капсюлях/бликах.
    Фон неоднородный (оценка силуэта ненадёжна, `_foreground_bbox` вернул `None`) ->
    fallback на центрально-нижнюю часть кадра. Никогда не бросает.
    """
    h, w = image.shape[:2]
    bbox = _foreground_bbox(image)
    if bbox is None:
        return _order_corners(_fallback_region(w, h))

    x, y, bw, bh = bbox
    y0 = y + 0.30 * bh  # капсюль/горлышко/плечики
    y1 = y + 0.94 * bh  # тонкая полоса основания
    margin_x = 0.05 * bw
    x0 = max(0, x - margin_x)
    x1 = min(w, x + bw + margin_x)
    region = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    return _order_corners(region)


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(d)]
    bl = pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def crop_to_label(image: np.ndarray, margin: float = 0.08) -> np.ndarray:
    """Axis-aligned кроп по bounding box `detect_label_region()` (+`margin` с каждой
    стороны). Проще, чем `unwarp_label` — для эталонов каталога (студийные фото, без
    выраженной перспективы), где полноценный перспективный warp не нужен; используется
    аугментатором как первый шаг перед наклейкой на цилиндр."""
    h, w = image.shape[:2]
    corners = detect_label_region(image)
    x0, y0 = corners[:, 0].min(), corners[:, 1].min()
    x1, y1 = corners[:, 0].max(), corners[:, 1].max()
    bw, bh = x1 - x0, y1 - y0
    x0 = max(0, x0 - margin * bw)
    x1 = min(w, x1 + margin * bw)
    y0 = max(0, y0 - margin * bh)
    y1 = min(h, y1 + margin * bh)
    xi0, yi0, xi1, yi1 = int(x0), int(y0), int(round(x1)), int(round(y1))
    if xi1 - xi0 < 4 or yi1 - yi0 < 4:
        return image
    return image[yi0:yi1, xi0:xi1]


def _cylinder_unbend_map(width: int, height: int, strength: float) -> tuple[np.ndarray, np.ndarray]:
    """Горизонтальная карта remap, приближённо компенсирующая ракурсное сжатие
    краёв цилиндрической этикетки: смешивает identity с `sin(x*pi/2)` (монотонна,
    сохраняет края [-1,1] на месте, без разрывов/сингулярностей). При `strength=0`
    вырождается в identity (чистая перспектива без цилиндрической поправки).

    Направление: производная smaller у края (растягиваем — то, что на фото сжато
    ракурсом ближе к силуэту бутылки, разворачиваем обратно) и больше у центра
    (слегка поджимаем) — величина `strength` эмпирическая (нет доступа к истинному
    радиусу конкретной бутылки по одному 2D-фото), подобрана как умеренная правка."""
    xs = np.linspace(-1, 1, width, dtype=np.float64)
    corrected = (1 - strength) * xs + strength * np.sin(xs * np.pi / 2)
    src_x = (corrected + 1) / 2 * (width - 1)
    map_x = np.tile(src_x.astype(np.float32), (height, 1))
    map_y = np.tile(np.arange(height, dtype=np.float32).reshape(-1, 1), (1, width))
    return map_x, map_y


def unwarp_label(image: np.ndarray, corners: np.ndarray, out_size: int = NORM_SIZE_DEFAULT, strength: float = 0.35) -> np.ndarray:
    """Кроп по `corners` (перспективное выравнивание в прямоугольник) + приближённая
    обратная развёртка цилиндра (`_cylinder_unbend_map`) в канонический квадратный канвас.

    Точная развёртка требует истинного радиуса бутылки и позы камеры — в общем случае
    невосстановимо из одного 2D-фото без доп. данных (глубины/нескольких ракурсов).
    Это практичное классическое приближение: перспективное выравнивание 4 углов решает
    основную часть искажения (наклон камеры), `_cylinder_unbend_map` — остаточную
    цилиндрическую составляющую.
    """
    tl, tr, br, bl = corners
    width = max(8, int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    height = max(8, int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))

    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    M = cv2.getPerspectiveTransform(corners, dst)
    cropped = cv2.warpPerspective(image, M, (width, height))

    map_x, map_y = _cylinder_unbend_map(width, height, strength)
    corrected = cv2.remap(cropped, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    return cv2.resize(corrected, (out_size, out_size), interpolation=cv2.INTER_AREA)


def normalize_illumination(image: np.ndarray) -> np.ndarray:
    """CLAHE на L-канале (LAB) + мягкий серый мир для баланса белого — сглаживает
    локальные блики/тени и цветовой сдвиг от освещения полки, не трогая геометрию.

    НАМЕРЕННО мягкая (`clipLimit=1.2`, узкий диапазон gain 0.92-1.08): первая версия
    (`clipLimit=2.5`, gain 0.8-1.25 — стандартные учебниковые значения для "убрать
    блики совсем") на self-match дев-фикстур оказалась ГЛАВНОЙ причиной просадки
    (~84% вместо ~96% без нормализации вовсе, см. reports/g-report.md, "Предположения")
    — агрессивный локальный контраст и пересчёт баланса белого заметно уводят
    SigLIP2-эмбеддинг от "естественного" распределения, на котором модель обучалась,
    сильнее, чем помогает выравнивание освещения. Мягкая версия — компромисс: всё ещё
    сглаживает выраженные блики/цветовой сдвиг (то, о чём просит case.md), но не
    перекрашивает изображение агрессивно. Порог обоснован эмпирически на дев-фикстурах;
    релевантность на РЕАЛЬНЫХ фото кейса (не синтетике) — открытый вопрос до приезда
    датасета, см. "Предложения к контрактам"."""
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.2, tileGridSize=(8, 8))
    l = clahe.apply(l)
    balanced = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2RGB)

    means = balanced.reshape(-1, 3).astype(np.float64).mean(axis=0)
    gray = means.mean()
    gains = np.clip(gray / np.clip(means, 1e-3, None), 0.92, 1.08)
    return np.clip(balanced.astype(np.float64) * gains[None, None, :], 0, 255).astype(np.uint8)


def normalize_query(image: np.ndarray, *, enabled: bool = True, out_size: int = NORM_SIZE_DEFAULT) -> np.ndarray:
    """Полный пайплайн запроса: детект → кроп → развёртка цилиндра → фотометрия.

    `enabled=False` — флаг контракта для A/B в eval: пропускает геометрию и фотометрию,
    отдаёт только letterbox-ресайз в тот же канонический канвас (единственная разница
    между режимами — именно нормализация, не размер входа энкодера).
    """
    if not enabled:
        return imageio.letterbox_resize(image, out_size, pad_value=0)
    corners = detect_label_region(image)
    unwarped = unwarp_label(image, corners, out_size=out_size)
    return normalize_illumination(unwarped)
