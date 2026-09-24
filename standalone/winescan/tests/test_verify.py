import numpy as np
from PIL import Image, ImageDraw

from winescan.search.local_match import extract, prepare
from winescan.search.verify import verify


def _label(seed: int, tint=(255, 255, 255)) -> Image.Image:
    """Эталон: текстурная этикетка на прозрачном фоне; tint — цвет светлой части этикетки."""
    rng = np.random.default_rng(seed)
    texture = Image.fromarray(rng.integers(0, 255, (60, 40, 3), dtype=np.uint8)).resize((400, 600), Image.NEAREST)
    draw = ImageDraw.Draw(texture)
    draw.rectangle([40, 200, 360, 420], fill=tint)
    draw.text((60, 280), "MUSKATEL 2023", fill="black")
    rgba = texture.convert("RGBA")
    return rgba


def _photo_of(label: Image.Image) -> Image.Image:
    """«Фото»: та же этикетка на сером фоне под перспективой и в другом масштабе."""
    background = Image.new("RGB", (400, 600), (90, 90, 90))
    background.paste(label, (0, 0), label)
    warped = background.transform((400, 600), Image.Transform.QUAD, (12, 6, 0, 594, 388, 600, 400, 0), Image.BICUBIC)
    return warped.resize((340, 510))


def _features(image: Image.Image):
    prepared = prepare(image)
    return prepared, extract(prepared)


def test_same_label_verifies_better_than_other_label():
    reference = _label(1)
    other = _label(2)
    query_image, query_features = _features(_photo_of(reference))
    ref_image, ref_features = _features(reference)
    other_image, other_features = _features(other)

    same = verify(query_image, query_features, ref_image, ref_features)
    different = verify(query_image, query_features, other_image, other_features)

    assert same.inliers > 3 * max(different.inliers, 1)
    assert same.coverage > different.coverage
    assert same.ncc > 0.5


def test_color_distance_separates_label_tints_with_same_layout():
    white = _label(1, tint=(250, 250, 245))
    pink = _label(1, tint=(250, 170, 190))  # та же вёрстка и текстура, другой цвет этикетки
    query_image, query_features = _features(_photo_of(white))
    white_image, white_features = _features(white)
    pink_image, pink_features = _features(pink)

    to_white = verify(query_image, query_features, white_image, white_features)
    to_pink = verify(query_image, query_features, pink_image, pink_features)

    assert to_white.color_distance < to_pink.color_distance
