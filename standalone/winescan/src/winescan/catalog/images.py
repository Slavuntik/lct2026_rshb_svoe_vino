"""Техническое описание изображений: размеры, прозрачность, фон, хеши.

Нужно, чтобы разрешать неоднозначные привязки фото (одинаковое ли содержимое,
похоже ли на студийное фото бутылки) и искать визуальные дубли в каталоге.
"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import imagehash
import numpy as np
import pandas as pd
from PIL import Image


def has_transparency(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def to_rgb_on_white(image: Image.Image) -> Image.Image:
    """RGB-копия; прозрачный фон (эталоны с вырезанным фоном) заливается белым."""
    if has_transparency(image):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def inspect_image(path: Path) -> dict:
    data = path.read_bytes()
    info: dict = {"filename": path.name, "sha256": hashlib.sha256(data).hexdigest(), "error": None}
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            info.update(width=image.width, height=image.height, mode=image.mode, format=image.format)
            if has_transparency(image):
                alpha = np.asarray(image.convert("RGBA").getchannel("A"))
                info["transparent_share"] = float((alpha < 250).mean())
            else:
                info["transparent_share"] = 0.0
            rgb = to_rgb_on_white(image)
    except Exception as exc:  # битый или неподдерживаемый файл не должен ронять сборку
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    rgb.thumbnail((256, 256))
    gray = np.asarray(rgb.convert("L"), dtype=np.float32)
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    info["border_mean"] = float(border.mean())
    info["border_std"] = float(border.std())
    info["phash"] = str(imagehash.phash(rgb))
    return info


def inspect_images(paths: Iterable[Path], workers: int | None = None) -> pd.DataFrame:
    paths = list(paths)
    workers = workers or min(16, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(inspect_image, paths, chunksize=16))
    return pd.DataFrame(rows)


def phash_distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def packshot_score(info: Mapping) -> int:
    """0..3: насколько картинка похожа на студийное фото бутылки, а не на скриншот или полку.

    +1 заметная прозрачность (фон вырезан), +1 вытянута по вертикали (h/w ≥ 1.3),
    +1 светлый однородный край кадра.
    """
    if info.get("error") or not info.get("width"):
        return 0
    # без «x or default»: идеально ровный фон даёт border_std == 0.0, а это falsy
    transparent_share, border_mean, border_std = (
        info.get("transparent_share"), info.get("border_mean"), info.get("border_std")
    )
    score = int(transparent_share is not None and transparent_share > 0.05)
    score += int(info["height"] / info["width"] >= 1.3)
    score += int(border_mean is not None and border_std is not None and border_mean > 200 and border_std < 12)
    return score
