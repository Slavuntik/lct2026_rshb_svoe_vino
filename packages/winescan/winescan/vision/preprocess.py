"""Слой 1: нормализация изображений перед извлечением признаков.

Эталон и запрос приводятся к одному виду — упаковка, вписанная в белый квадрат с полями.
Для эталона упаковку даёт альфа-канал или белый фон, для запроса — рамка детектора.
Квадрат нужен, потому что процессор SigLIP растягивает кадр до квадрата: без полей
вытянутая бутылка (h/w ≈ 3,5) исказилась бы.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

Box = tuple[float, float, float, float]


def cutout(image: Image.Image, white_threshold: int = 235) -> Image.Image:
    """RGBA-вырезка упаковки, обрезанная по содержимому.

    Если фон уже прозрачный — берём альфа-канал. Иначе прозрачным делаем только почти белый
    фон, связанный с краем кадра: белые места на самой этикетке остаются непрозрачными.
    """
    rgba = image.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A")).copy()
    if (alpha < 250).mean() <= 0.05:
        rgb = np.asarray(rgba.convert("RGB"))
        near_white = rgb.min(axis=2) > white_threshold
        labels, _ = ndimage.label(near_white)
        border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
        background = np.isin(labels, border[border > 0])
        alpha = np.where(background, 0, 255).astype(np.uint8)
        rgba.putalpha(Image.fromarray(alpha))
    bbox = Image.fromarray(alpha).point(lambda a: 255 if a > 10 else 0).getbbox()
    return rgba.crop(bbox) if bbox else rgba


def fit_on_square(image: Image.Image, margin: float = 0.04, fill=(255, 255, 255)) -> Image.Image:
    """Вписывает изображение в квадрат с полями на белом фоне (RGBA — с учётом альфы)."""
    side = round(max(image.size) * (1 + 2 * margin))
    canvas = Image.new("RGB", (side, side), fill)
    offset = ((side - image.width) // 2, (side - image.height) // 2)
    if image.mode == "RGBA":
        canvas.paste(image, offset, image)
    else:
        canvas.paste(image.convert("RGB"), offset)
    return canvas


def crop_box(image: Image.Image, box: Box, margin: float = 0.03) -> Image.Image:
    x0, y0, x1, y1 = box
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    return image.crop(
        (round(max(0, x0 - mx)), round(max(0, y0 - my)), round(min(image.width, x1 + mx)), round(min(image.height, y1 + my)))
    )


VIEWS = ("full", "label")
LABEL_REGION = (0.40, 0.97)  # доля высоты высокой упаковки, где обычно основная этикетка


def label_region(image: Image.Image) -> Image.Image:
    """Нижняя часть высокой упаковки (бутылки), где обычно основная этикетка.

    У вин одной серии отличается именно этикетка; на эмбеддинге всей бутылки эта разница
    размывается горлышком и стеклом. Невысокие упаковки (коробки, банки) не режем.
    """
    width, height = image.size
    if height / max(width, 1) < 1.8:
        return image
    return image.crop((0, round(height * LABEL_REGION[0]), width, round(height * LABEL_REGION[1])))


def _view(package: Image.Image, view: str) -> Image.Image:
    if view not in VIEWS:
        raise ValueError(f"неизвестный вид {view!r}, ожидается один из {VIEWS}")
    return fit_on_square(label_region(package) if view == "label" else package)


def reference_view(image: Image.Image, view: str = "full") -> Image.Image:
    """Эталон каталога -> упаковка (или её этикетка) в белом квадрате."""
    return _view(cutout(image), view)


def query_view(image: Image.Image, box: Box | None = None, view: str = "full") -> Image.Image:
    """Фото пользователя -> (кроп по рамке) -> (этикетка) -> белый квадрат. Учитывает EXIF-поворот."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    return _view(crop_box(image, box) if box else image, view)
