"""Синтетические «полевые» фото из эталонов каталога.

Настоящих полевых фото в пакете кейса 3, поэтому для измерения точности и дообучения
кадр у полки имитируется: вырезанная упаковка из эталона ставится на фоновое фото,
рядом — соседние бутылки (часто той же винодельни), затем перспектива, наклон, свет,
блики, размытие, шум и JPEG.

Пресет ``v2`` добавляет то, чего не было в ``v1`` и что делает кадр ближе к реальному:
поворот бутылки вокруг оси по цилиндрической модели (этикетка «уходит» за край, дуги строк,
затенение, вертикальный блик стекла — winescan.vision.cylinder), тесные и перекрывающиеся
соседи, полосу ценников у края полки и смаз от движения.

Пресет ``v3`` бьёт по главной поблажке: в кадре лежат пиксели самого эталона, и любой
сопоставитель локальных признаков от этого выигрывает. Целевая упаковка (и только она) портится
так, как её меняет другая партия печати и пересъёмка: сдвиг тона и насыщенности, гамма, растр,
цикл «размытие — резкость», лишние пересжатия. Вёрстка и надписи остаются, попиксельное
совпадение исчезает — это даёт защищаемую **нижнюю** границу точности.

Ограничение: отражения окружения и другую геометрию съёмки генератор всё равно не воспроизводит,
поэтому даже v3 не заменяет полевые фото.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, replace

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from winescan.validation.cylinder_alt import rotate_cylinder_perspective
from winescan.vision.cylinder import rotate_cylinder
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
    # v2: всё ниже выключено в v1, чтобы старая выборка воспроизводилась с тем же seed
    yaw_deg: float = 0.0
    arc_pitch: float = 0.0
    cylinder_shading: float = 0.0
    bottle_highlight_prob: float = 0.0
    neighbour_overlap: float = 0.0  # доля ширины соседа, на которую он может заходить за целевую бутылку
    price_strip_prob: float = 0.0
    motion_blur_prob: float = 0.0
    # v3: «другой тираж» целевой упаковки; в v1 и v2 выключено, чтобы прежние выборки
    # воспроизводились с тем же seed
    reprint_hue: float = 0.0  # сдвиг тона, единицы HSV PIL (256 на круг)
    reprint_saturation: float = 0.0  # относительный разброс насыщенности
    reprint_gamma: float = 0.0  # относительный разброс гаммы
    halftone_prob: float = 0.0
    resharpen_prob: float = 0.0
    recompress_rounds: int = 0
    # проверка оговорки пункта 3.3 плана: искривление запроса моделью, не связанной с галереей
    warp: str = "cylinder"  # "cylinder" — vision.cylinder (ею же строится галерея); "perspective" — validation.cylinder_alt
    camera_distance: float = 5.0  # расстояние камеры в радиусах бутылки; влияет только при warp="perspective"


SYNTH_PRESETS = {
    "v1": SynthConfig(),
    "v2": replace(
        SynthConfig(),
        target_height=(0.4, 0.95),
        max_neighbours=4,
        perspective=0.05,
        yaw_deg=35.0,
        arc_pitch=0.3,
        cylinder_shading=0.35,
        bottle_highlight_prob=0.6,
        neighbour_overlap=0.25,
        price_strip_prob=0.4,
        motion_blur_prob=0.2,
    ),
}
SYNTH_PRESETS["v3"] = replace(
    SYNTH_PRESETS["v2"],
    reprint_hue=6.0,
    reprint_saturation=0.18,
    reprint_gamma=0.22,
    halftone_prob=0.7,
    resharpen_prob=0.7,
    recompress_rounds=2,
)
# то же, что v2, но запрос искривляется моделью, не связанной с построением галереи ракурсов:
# проверка оговорки пункта 3.3 плана. Порядок розыгрышей тот же, поэтому кадры парные с v2
SYNTH_PRESETS["v2p"] = replace(SYNTH_PRESETS["v2"], warp="perspective")


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


def _cylinder(sprite: Image.Image, rng: random.Random, config: SynthConfig) -> Image.Image:
    # коробки, тетрапаки и банки невысокие и не цилиндры: их не поворачиваем (порог как у вида «этикетка»)
    if not config.yaw_deg or sprite.height / max(sprite.width, 1) < 1.8:
        return sprite
    highlight = rng.uniform(-50, 50) if rng.random() < config.bottle_highlight_prob else None
    # порядок розыгрышей один для обеих моделей: иначе выборки перестанут быть парными
    yaw = rng.uniform(-config.yaw_deg, config.yaw_deg)
    pitch = rng.uniform(-config.arc_pitch, config.arc_pitch)
    shading = rng.uniform(0, config.cylinder_shading)
    # на пробном листе блик 0,8 засвечивал бутылку почти целиком
    strength = rng.uniform(0.25, 0.6)
    if config.warp == "perspective":
        return rotate_cylinder_perspective(sprite, yaw_deg=yaw, camera_distance=config.camera_distance,
                                           pitch=pitch, shading=shading, highlight_angle_deg=highlight,
                                           highlight_strength=strength)  # fmt: skip
    return rotate_cylinder(sprite, yaw_deg=yaw, pitch=pitch, shading=shading,
                           highlight_angle_deg=highlight, highlight_strength=strength)  # fmt: skip


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


def _price_strip(canvas: Image.Image, top: int, rng: random.Random) -> None:
    """Полоса края полки с ценниками: светлые прямоугольники с цифрами."""
    draw = ImageDraw.Draw(canvas)
    strip_h = rng.randint(40, 90)
    draw.rectangle([0, top, canvas.width, top + strip_h], fill=tuple(rng.randint(30, 90) for _ in range(3)))
    x = rng.randint(-60, 20)
    while x < canvas.width:
        tag_w = rng.randint(120, 220)
        color = rng.choice([(250, 250, 245), (255, 230, 60), (255, 255, 255), (240, 60, 60)])
        draw.rectangle([x, top + 5, x + tag_w, top + strip_h - 5], fill=color)
        draw.text((x + 10, top + strip_h // 3), f"{rng.randint(390, 4990)},99", fill=(20, 20, 20))
        x += tag_w + rng.randint(40, 200)


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


def _motion_blur(canvas: Image.Image, rng: random.Random) -> Image.Image:
    length = rng.randint(5, 17)
    kernel = np.zeros((length, length), dtype=np.float32)
    kernel[length // 2, :] = 1.0 / length
    rotation = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), rng.uniform(0, 180), 1.0)
    kernel = cv2.warpAffine(kernel, rotation, (length, length))
    kernel /= max(kernel.sum(), 1e-6)
    return Image.fromarray(cv2.filter2D(np.asarray(canvas), -1, kernel))


def _reprint(sprite: Image.Image, rng: random.Random, config: SynthConfig) -> Image.Image:
    """«Другой тираж» этикетки: портит только целевую упаковку, сохраняя вёрстку и надписи.

    Нужен, чтобы убрать попиксельное совпадение запроса с эталоном — главную поблажку синтетики.
    Прозрачность сохраняется: фон вырезки не должен появиться из-под искажений."""
    if not any((config.reprint_hue, config.reprint_saturation, config.reprint_gamma,
                config.halftone_prob, config.resharpen_prob, config.recompress_rounds)):  # fmt: skip
        return sprite
    alpha = sprite.getchannel("A")
    rgb = sprite.convert("RGB")

    if config.reprint_hue or config.reprint_saturation:
        hsv = np.asarray(rgb.convert("HSV"), dtype=np.int16)
        hsv[..., 0] = (hsv[..., 0] + round(rng.uniform(-config.reprint_hue, config.reprint_hue))) % 256
        if config.reprint_saturation:
            hsv[..., 1] = np.clip(hsv[..., 1] * (1 + rng.uniform(-config.reprint_saturation, config.reprint_saturation)), 0, 255)
        rgb = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")
    if config.reprint_gamma:
        gamma = 1 + rng.uniform(-config.reprint_gamma, config.reprint_gamma)
        rgb = rgb.point([min(255, round(255 * (value / 255) ** gamma)) for value in range(256)] * 3)
    if config.halftone_prob and rng.random() < config.halftone_prob:
        # растр печати: слабая регулярная сетка, как на бумажной этикетке вблизи
        raster = (np.indices((rgb.height, rgb.width)).sum(axis=0) % 2)[..., None] * 2 - 1
        array = np.asarray(rgb, dtype=np.int16) + raster * rng.randint(3, 9)
        rgb = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB")
    if config.resharpen_prob and rng.random() < config.resharpen_prob:
        rgb = rgb.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.4)))
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=rng.randint(80, 170)))
    for _ in range(config.recompress_rounds):
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=rng.randint(55, 85))
        rgb = Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")

    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


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
    if config.motion_blur_prob and rng.random() < config.motion_blur_prob:
        canvas = _motion_blur(canvas, rng)
    elif rng.random() < config.blur_prob:
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
    # «другой тираж» — только для целевой упаковки: соседи остаются как есть.
    # Случайность для него берётся из отдельного генератора, выведенного из состояния основного
    # без его расходования. Иначе порча печати сдвигает всю дальнейшую последовательность, и при
    # одном посеве выборки различаются сценой, а не тиражом: сравнивать кадры попарно нельзя.
    reprint_rng = random.Random(repr(rng.getstate()[1][:8]))
    sprite = _cylinder(_reprint(_scale_to_height(cutout(target), target_h), reprint_rng, config), rng, config)
    sprite = _warp(sprite, rng, config.perspective)
    sprite = sprite.rotate(rng.uniform(-config.rotation_deg, config.rotation_deg), expand=True,
                           resample=Image.Resampling.BICUBIC)  # fmt: skip
    x0 = round(w * rng.uniform(0.35, 0.65)) - sprite.width // 2
    y0 = rng.randint(round(-0.08 * sprite.height), max(0, h - round(0.92 * sprite.height)))

    left, right = x0, x0 + sprite.width
    in_front = []
    for index, neighbour in enumerate(neighbours):
        other = _cylinder(_scale_to_height(cutout(neighbour), round(target_h * rng.uniform(0.85, 1.1))), rng, config)
        overlap = round(other.width * config.neighbour_overlap)
        gap = rng.randint(-other.width // 5 - overlap, other.width // 4)
        if index % 2 == 0:
            nx, left = left - other.width - gap, left - other.width - gap
        else:
            nx, right = right + gap, right + gap + other.width
        position = (nx, y0 + rng.randint(-target_h // 20, target_h // 20))
        # при перекрытии часть соседей стоит перед целевой бутылкой, как на тесной полке
        if overlap and rng.random() < 0.3:
            in_front.append((other, position))
        else:
            canvas.paste(other, position, other)

    canvas.paste(sprite, (x0, y0), sprite)
    for other, position in in_front:
        canvas.paste(other, position, other)
    sx0, sy0, sx1, sy1 = sprite.getchannel("A").getbbox() or (0, 0, sprite.width, sprite.height)
    box = (max(0, x0 + sx0), max(0, y0 + sy0), min(w, x0 + sx1), min(h, y0 + sy1))
    if config.price_strip_prob and rng.random() < config.price_strip_prob and box[3] < h - 40:
        _price_strip(canvas, min(h - 40, box[3] - rng.randint(0, 30)), rng)
    return _photometric(canvas, box, rng, config), box
