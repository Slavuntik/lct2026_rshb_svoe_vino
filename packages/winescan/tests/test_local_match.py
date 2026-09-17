import numpy as np
from PIL import Image, ImageDraw

from winescan.search.local_match import extract, inliers, local_bonus


def _label(text: str, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    image = Image.fromarray(rng.integers(0, 255, (60, 40, 3), dtype=np.uint8)).resize((400, 600), Image.NEAREST)
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 200, 360, 420], fill="white")
    draw.text((60, 280), text, fill="black")
    return image


def test_warped_copy_has_many_more_inliers_than_other_label():
    original = _label("ALIGOTE 2024", seed=1)
    # тот же кадр под небольшой перспективой и в другом масштабе
    warped = original.transform((400, 600), Image.Transform.QUAD, (10, 5, 0, 595, 390, 600, 400, 0), Image.BICUBIC)
    warped = warped.resize((320, 480))
    other = _label("ALIGOTE 2024", seed=2)

    same = inliers(extract(warped), extract(original))
    different = inliers(extract(warped), extract(other))

    assert same > 30
    assert same > 3 * max(different, 1)


def test_blank_images_have_no_inliers():
    blank = Image.new("RGB", (300, 300), "white")

    assert inliers(extract(blank), extract(blank)) == 0


def test_local_bonus_is_monotonic_and_saturates():
    assert local_bonus(0) == 0.0
    assert local_bonus(10) < local_bonus(50) < local_bonus(150) == local_bonus(1000) == 1.0
