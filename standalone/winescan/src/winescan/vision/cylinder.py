"""Цилиндрическая модель бутылки: новый ракурс из одного фронтального изображения.

По мотивам Huang et al., «Single-image driven 3D viewpoint training data augmentation for
effective wine label recognition» (arXiv 2404.08820): там этикетка с фото переносится на
цилиндр в новой позе, и top-1 растёт с 76,4% до 91,2% относительно 2D-аугментации.

У нас эталоны — фронтальные студийные фото с вырезанным фоном, поэтому модель проще:
каждая строка силуэта — сечение цилиндра со своим радиусом (полуширина маски; горлышко уже
корпуса). Столбец изображения соответствует углу β на поверхности: x = c + R·sin β.
Поворот бутылки на угол θ вокруг вертикальной оси: пиксель выхода с углом β берётся из
исходного угла β − θ; скрытые за краем точки становятся прозрачными. Наклон камеры
изгибает горизонтали этикетки в дуги, затенение и вертикальный блик имитируют стекло.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def silhouette_rows(alpha: np.ndarray, threshold: int = 128, smooth: int = 15) -> tuple[np.ndarray, np.ndarray]:
    """Центр и полуширина силуэта в каждой строке (сглажены по вертикали); пустые строки — полуширина 0."""
    mask = alpha >= threshold
    width = mask.shape[1]
    present = mask.any(axis=1)
    left = np.where(present, mask.argmax(axis=1), 0).astype(np.float32)
    right = np.where(present, width - 1 - mask[:, ::-1].argmax(axis=1), 0).astype(np.float32)
    center, half = (left + right) / 2, np.where(present, (right - left) / 2, 0.0).astype(np.float32)
    if smooth > 1 and present.any():
        kernel = np.ones(smooth, dtype=np.float32) / smooth
        weights = np.convolve(present.astype(np.float32), kernel, mode="same")
        safe = np.maximum(weights, 1e-6)
        center = np.where(present, np.convolve(center * present, kernel, mode="same") / safe, center)
        half = np.where(present, np.convolve(half * present, kernel, mode="same") / safe, 0.0)
    return center.astype(np.float32), half.astype(np.float32)


def rotate_cylinder(
    sprite: Image.Image,
    yaw_deg: float,
    pitch: float = 0.0,
    shading: float = 0.0,
    highlight_angle_deg: float | None = None,
    highlight_strength: float = 0.6,
    highlight_width_deg: float = 6.0,
) -> Image.Image:
    """RGBA-вырезка упаковки (фронтальный вид) -> вид после поворота на ``yaw_deg`` вокруг оси бутылки.

    ``pitch`` — сила изгиба горизонталей в дуги (0 — камера на уровне этикетки, ~0,3 — заметно
    сверху или снизу); ``shading`` — затемнение к краям (0..1); ``highlight_angle_deg`` — угол
    вертикального блика на стекле (None — без блика).
    """
    rgba = np.asarray(sprite.convert("RGBA"))
    height, width = rgba.shape[:2]
    center, half = silhouette_rows(rgba[..., 3])
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    radius = np.maximum(half, 1.0)[:, None]
    offset = xs - center[:, None]
    inside = np.abs(offset) <= half[:, None]
    beta = np.arcsin(np.clip(offset / radius, -1.0, 1.0))
    source_angle = beta - np.radians(yaw_deg)
    visible = inside & (np.abs(source_angle) <= np.pi / 2)

    map_x = center[:, None] + radius * np.sin(source_angle)
    sag = radius * (1.0 - np.cos(beta))
    map_y = ys - pitch * (ys - height / 2) / max(height, 1) * sag * 4.0
    out = cv2.remap(rgba, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)).astype(np.float32)  # fmt: skip

    if shading:
        out[..., :3] *= (1.0 - shading * (1.0 - np.cos(beta)))[..., None]
    if highlight_angle_deg is not None:
        glare = highlight_strength * np.exp(-((beta - np.radians(highlight_angle_deg)) ** 2)
                                            / (2 * np.radians(highlight_width_deg) ** 2))  # fmt: skip
        out[..., :3] += (255.0 - out[..., :3]) * glare[..., None]
    # вне видимой части — полностью прозрачный чёрный: иначе в прозрачных пикселях остаётся цвет
    # ближайшего края бутылки и «проступает» при масштабировании и переносе на фон
    out = np.where(visible[..., None], out, 0.0)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
