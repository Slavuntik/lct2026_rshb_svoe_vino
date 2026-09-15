"""Синтетические «полевые» фото из эталонов каталога.

Настоящих полевых фото в пакете кейса 3, поэтому для измерения точности и дообучения
кадр у полки имитируется: вырезанная упаковка из эталона ставится на фоновое фото,
рядом — соседние бутылки (часто той же винодельни), затем перспектива, наклон, свет,
блики, размытие, шум и JPEG.

Ограничение: в кадре те же пиксели, что в эталоне, поэтому метрики на синтетике
оптимистичнее реальных. Реальные отличия (другой тираж этикетки, изгиб бутылки,
отражения стекла) генератор не воспроизводит.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from winescan.vision.preprocess import cutout


@dataclass(frozen=True)
class SynthConfig:
    canvas: tuple[int, int] = (1080, 1440)  # ширина, высота: 3:4, как у фото с телефона
    target_height: tuple[float, float] = (0.5, 0.95)
    max_neighbours: int = 3
    same_winery_neighbours_prob: float = 0.5
    perspective: float = 0.08
    rotation_deg: float = 8.0
    glare_prob: float = 0.5
    blur_prob: float = 0.5
    jpeg_quality: tuple[int, int] = (35, 90)
    synthetic_background_prob: float = 0.15


def perspective_coefficients(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    """Коэффициенты PIL PERSPECTIVE: точка результата ``dst[i]`` берётся из ``src[i]``."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    return np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float)).tolist()


def _scale_to_height(sprite: Image.Image, height: int) -> Image.Image:
    height = max(8, height)
    width = max(4, round(sprite.width * height / sprite.height))
    return sprite.resize((width, height), Image.Resampling.LANCZOS)


def _warp(sprite: Image.Image, rng: random.Random, strength: float) -> Image.Image:
    w, h = sprite.size
    dx, dy = strength * w, strength * h * 0.5
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    dst = [
        (rng.uniform(0, dx), rng.uniform(0, dy)),
        (w - rng.uniform(0, dx), rng.uniform(0, dy)),
        (w - rng.uniform(0, dx), h - rng.uniform(0, dy)),
        (rng.uniform(0, dx), h - rng.uniform(0, dy)),
    ]
    return sprite.transform((w, h), Image.Transform.PERSPECTIVE, perspective_coefficients(src, dst),
                            Image.Resampling.BICUBIC)  # fmt: skip


def _background(photo: Image.Image | None, size: tuple[int, int], rng: random.Random) -> Image.Image:
    w, h = size
    if photo is None:
        # «полка»: тёмный градиент с горизонтальными перекладинами
        top, bottom = rng.randint(20, 90), rng.randint(60, 160)
        gradient = np.linspace(top, bottom, h, dtype=np.float32)[:, None, None]
        tint = np.array([rng.uniform(0.8, 1.2), rng.uniform(0.7, 1.0), rng.uniform(0.5, 0.9)], dtype=np.float32)
        array = np.clip(np.repeat(gradient, w, axis=1) * tint, 0, 255).astype(np.uint8)
        canvas = Image.fromarray(array)
        draw = ImageDraw.Draw(canvas)
        for _ in range(rng.randint(1, 3)):
            y = rng.randint(0, h)
            draw.rectangle([0, y, w, y + rng.randint(10, 40)], fill=tuple(rng.randint(90, 200) for _ in range(3)))
        return canvas
    photo = photo.convert("RGB")
    scale = max(w / photo.width, h / photo.height) * rng.uniform(1.0, 1.6)
    photo = photo.resize((max(w, round(photo.width * scale)), max(h, round(photo.height * scale))),
                         Image.Resampling.BILINEAR)  # fmt: skip
    x, y = rng.randint(0, photo.width - w), rng.randint(0, photo.height - h)
    return photo.crop((x, y, x + w, y + h))


def _glare(canvas: Image.Image, box: tuple[int, int, int, int], rng: random.Random) -> Image.Image:
    x0, y0, x1, y1 = box
    mask = Image.new("L", canvas.size, 0)
    draw = ImageDraw.Draw(mask)
    for _ in range(rng.randint(1, 2)):
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        rx, ry = rng.uniform(0.05, 0.3) * (x1 - x0 + 1), rng.uniform(0.05, 0.25) * (y1 - y0 + 1)
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=rng.randint(120, 230))
    mask = mask.filter(ImageFilter.GaussianBlur(radius=rng.uniform(8, 30)))
    return Image.composite(Image.new("RGB", canvas.size, (255, 252, 240)), canvas, mask)


def _photometric(canvas: Image.Image, box, rng: random.Random, config: SynthConfig) -> Image.Image:
    canvas = ImageEnhance.Brightness(canvas).enhance(rng.uniform(0.55, 1.3))
    canvas = ImageEnhance.Contrast(canvas).enhance(rng.uniform(0.7, 1.25))
    canvas = ImageEnhance.Color(canvas).enhance(rng.uniform(0.7, 1.3))
    gains = np.array([rng.uniform(0.85, 1.15), 1.0, rng.uniform(0.85, 1.15)], dtype=np.float32)
    array = np.asarray(canvas, dtype=np.float32) * gains
    if rng.random() < config.glare_prob:
        canvas = _glare(Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)), box, rng)
        array = np.asarray(canvas, dtype=np.float32)
    array += np.random.default_rng(rng.randrange(2**32)).normal(0, rng.uniform(0, 8), array.shape)
    canvas = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    if rng.random() < config.blur_prob:
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.5, 2.5)))
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=rng.randint(*config.jpeg_quality))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def render_sample(
    target: Image.Image,
    neighbours: list[Image.Image],
    background: Image.Image | None,
    rng: random.Random,
    config: SynthConfig = SynthConfig(),
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Кадр «у полки» и bbox целевой упаковки (x0, y0, x1, y1) в пикселях кадра."""
    w, h = config.canvas
    canvas = _background(None if rng.random() < config.synthetic_background_prob else background, (w, h), rng)

    target_h = round(h * rng.uniform(*config.target_height))
    sprite = _warp(_scale_to_height(cutout(target), target_h), rng, config.perspective)
    sprite = sprite.rotate(rng.uniform(-config.rotation_deg, config.rotation_deg), expand=True,
                           resample=Image.Resampling.BICUBIC)  # fmt: skip
    x0 = round(w * rng.uniform(0.35, 0.65)) - sprite.width // 2
    y0 = rng.randint(round(-0.08 * sprite.height), max(0, h - round(0.92 * sprite.height)))

    left, right = x0, x0 + sprite.width
    for index, neighbour in enumerate(neighbours):
        other = _scale_to_height(cutout(neighbour), round(target_h * rng.uniform(0.85, 1.1)))
        gap = rng.randint(-other.width // 5, other.width // 4)
        if index % 2 == 0:
            nx, left = left - other.width - gap, left - other.width - gap
        else:
            nx, right = right + gap, right + gap + other.width
        canvas.paste(other, (nx, y0 + rng.randint(-target_h // 20, target_h // 20)), other)

    canvas.paste(sprite, (x0, y0), sprite)
    sx0, sy0, sx1, sy1 = sprite.getchannel("A").getbbox() or (0, 0, sprite.width, sprite.height)
    box = (max(0, x0 + sx0), max(0, y0 + sy0), min(w, x0 + sx1), min(h, y0 + sy1))
    return _photometric(canvas, box, rng, config), box
