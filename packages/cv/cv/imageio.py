"""Декодирование/кодирование изображений — единая точка входа для всего пакета.

Централизует "битые байты -> ValueError" (контракт DoD: `search()` на битом файле
обязан падать `ValueError`, не безымянным исключением PIL/cv2 и не 500-полуфабрикатом).
Используется всеми слоями (`augment`, `normalize`, `encoder`, `index`), чтобы гарантия
была одна на весь пакет, а не продублирована в каждом месте, где приходят внешние байты.
"""
from __future__ import annotations

import io
import struct

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

# `Image.load()` на некоторых обрезанных/битых файлах бросает исключения за пределами
# OSError/ValueError (напр. `struct.error` на обрезанном PNG-заголовке) — перечисляем
# явно, чтобы decode_image() гарантированно нормализовал ЛЮБОЙ битый вход в ValueError.
_DECODE_ERRORS = (UnidentifiedImageError, OSError, ValueError, SyntaxError, struct.error)


def decode_image(data: bytes) -> np.ndarray:
    """bytes -> RGB uint8 ndarray (H, W, 3). ValueError на что угодно нечитаемое.

    `Image.open()` сам по себе часто НЕ бросает исключение на битые/обрезанные файлы —
    ошибка всплывает только при реальном чтении пикселей, поэтому здесь всегда
    `im.load()` (не `im.verify()`, который декодирует не полностью и пропускает часть
    обрывов, см. документацию Pillow).
    """
    if not data:
        raise ValueError("cv.imageio.decode_image: пустые байты изображения")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            rgb = im.convert("RGB")
    except _DECODE_ERRORS as e:
        raise ValueError(f"cv.imageio.decode_image: битый или нераспознаваемый файл изображения ({e})") from e
    arr = np.asarray(rgb, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 3 or arr.shape[0] < 2 or arr.shape[1] < 2:
        raise ValueError(f"cv.imageio.decode_image: неожиданная форма декодированного изображения {arr.shape}")
    return arr


def load_image_file(path: str) -> np.ndarray:
    with open(path, "rb") as f:
        data = f.read()
    return decode_image(data)


def encode_jpeg(arr: np.ndarray, quality: int = 92) -> bytes:
    """RGB uint8 ndarray -> JPEG bytes (для кэша/сохранения синтетических ракурсов)."""
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def letterbox_resize(image: np.ndarray, out_size: int, pad_value: int = 0) -> np.ndarray:
    """Ресайз с сохранением пропорций в канонический квадратный канвас `out_size`.

    Общая утилита для "реального" ракурса аугментатора (`cv/augment.py::prepare_reference`,
    `pad_value=255` — белый фон, как у студийных фото каталога) и для normalize.py в
    режиме `enabled=False` (`pad_value=0` — нейтральный чёрный, раз геометрию не трогаем).
    """
    h, w = image.shape[:2]
    scale = out_size / max(h, w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (nw, nh), interpolation=interp)
    canvas = np.full((out_size, out_size, 3), pad_value, dtype=np.uint8)
    y0, x0 = (out_size - nh) // 2, (out_size - nw) // 2
    canvas[y0 : y0 + nh, x0 : x0 + nw] = resized
    return canvas
