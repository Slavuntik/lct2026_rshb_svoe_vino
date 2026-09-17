import random

import numpy as np
from PIL import Image, ImageDraw

from winescan.validation.synth import SynthConfig, perspective_coefficients, render_sample
from winescan.vision.preprocess import cutout


def _bottle_on_white() -> Image.Image:
    image = Image.new("RGB", (200, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([70, 40, 130, 560], fill=(60, 20, 30))
    draw.rectangle([75, 300, 125, 400], fill="white")  # белая этикетка внутри бутылки
    return image


def test_cutout_keeps_white_label_inside_bottle():
    sprite = cutout(_bottle_on_white())

    assert sprite.size == (61, 521)
    alpha = np.asarray(sprite.getchannel("A"))
    assert alpha[0, 0] == 255  # угол бутылки после обрезки
    assert alpha[300, 30] == 255  # белая этикетка не стала прозрачной


def test_cutout_uses_existing_alpha():
    image = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
    image.paste((200, 0, 0, 255), (20, 30, 80, 270))

    assert cutout(image).size == (60, 240)


def test_perspective_identity():
    corners = [(0, 0), (10, 0), (10, 20), (0, 20)]

    assert np.allclose(perspective_coefficients(corners, corners), [1, 0, 0, 0, 1, 0, 0, 0], atol=1e-9)


def test_render_sample_is_deterministic_and_box_inside_canvas():
    config = SynthConfig(canvas=(300, 400))
    background = Image.new("RGB", (640, 480), (90, 120, 150))
    args = (_bottle_on_white(), [_bottle_on_white()], background)

    image_a, box_a = render_sample(*args, random.Random(7), config)
    image_b, box_b = render_sample(*args, random.Random(7), config)

    assert image_a.size == (300, 400) and box_a == box_b
    assert np.array_equal(np.asarray(image_a), np.asarray(image_b))
    x0, y0, x1, y1 = box_a
    assert 0 <= x0 < x1 <= 300 and 0 <= y0 < y1 <= 400
