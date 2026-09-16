"""Вторая модель поворота бутылки — перспективная, независимая от `vision.cylinder`.

Зачем нужна. Мультиракурсная галерея строится функцией `vision.cylinder.rotate_cylinder`, и
синтетика поворачивает запросы **ею же**. Совпадает не библиотека, а закон проекции, поэтому
повёрнутый запрос и повёрнутый эталон согласованы по построению, и измеренный выигрыш галереи
(+2,4 п.п. на `synth_v2`) может быть артефактом общего кода. Этот модуль повторяет то же явление
— поворот вокруг вертикальной оси — другой геометрией, чтобы проверить оговорку пункта 3.3 плана.

Чем отличается. `vision.cylinder` проецирует ортографически: столбец ``x = c + R·sin β``, край
виден до ``β = 90°``. Здесь камера стоит на конечном расстоянии ``d = k·R`` (камера-обскура):

    x = c + f·R·sin β / (d − R·cos β)

Обращение аналитическое: ``f·R·sin β + u·R·cos β = u·d`` даёт
``β = arcsin(u·d / (R·√(f² + u²))) − arctan(u/f)``. Фокусное расстояние выбирается так, чтобы
ширина силуэта не менялась: ``f = R·√(k² − 1)``. Видимый край смещается — ``cos β = 1/k``, то есть
у близкой камеры видно заметно меньше половины поверхности, а сжатие к краю сильнее, чем при
ортографии. Вертикаль тоже ведёт себя иначе: дальние точки проецируются ближе к центру кадра
пропорционально глубине ``(k − cos β)``, а не по дуге ``R·(1 − cos β)``.

Общим с `vision.cylinder` остаётся только замер силуэта (`silhouette_rows`): радиус строки — это
свойство самой вырезки, а не модели проекции. В сервис модуль не входит: им пользуется только
генератор выборок.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from winescan.vision.cylinder import silhouette_rows


def rotate_cylinder_perspective(
    sprite: Image.Image,
    yaw_deg: float,
    camera_distance: float = 5.0,
    pitch: float = 0.0,
    shading: float = 0.0,
    highlight_angle_deg: float | None = None,
    highlight_strength: float = 0.6,
    highlight_width_deg: float = 6.0,
) -> Image.Image:
    """RGBA-вырезка (фронтальный вид) -> вид после поворота на ``yaw_deg`` при камере на ``camera_distance`` радиусов.

    ``camera_distance`` меньше ~1,5 лишено смысла: камера оказывается внутри бутылки, поэтому
    значение ограничивается снизу. Остальные параметры совпадают по смыслу с `vision.cylinder`,
    чтобы выборки отличались только законом проекции.
    """
    rgba = np.asarray(sprite.convert("RGBA"))
    height, width = rgba.shape[:2]
    center, half = silhouette_rows(rgba[..., 3])
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)

    k = max(float(camera_distance), 1.5)
    radius = np.maximum(half, 1.0)[:, None]
    focal = radius * np.sqrt(k * k - 1.0)
    offset = xs - center[:, None]
    inside = np.abs(offset) <= half[:, None]

    # угол на поверхности, попадающий в столбец x при съёмке с расстояния k·R
    ratio = offset * (k * radius) / (radius * np.sqrt(focal**2 + offset**2))
    beta = np.arcsin(np.clip(ratio, -1.0, 1.0)) - np.arctan2(offset, focal)
    limb = np.arccos(1.0 / k)

    source_angle = beta - np.radians(yaw_deg)
    visible = inside & (np.abs(beta) <= limb) & (np.abs(source_angle) <= np.pi / 2)

    map_x = center[:, None] + radius * np.sin(source_angle)
    # чем дальше точка от камеры, тем ближе она к центру кадра по вертикали
    depth = (k - np.cos(beta)) / (k - 1.0)
    map_y = ys + pitch * (ys - height / 2) * (depth - 1.0)
    out = cv2.remap(rgba, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32)  # fmt: skip

    if shading:
        out[..., :3] *= (1.0 - shading * (1.0 - np.cos(beta)))[..., None]
    if highlight_angle_deg is not None:
        glare = highlight_strength * np.exp(-((beta - np.radians(highlight_angle_deg)) ** 2)
                                            / (2 * np.radians(highlight_width_deg) ** 2))  # fmt: skip
        out[..., :3] += (255.0 - out[..., :3]) * glare[..., None]
    out = np.where(visible[..., None], out, 0.0)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
