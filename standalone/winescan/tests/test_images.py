import numpy as np
from PIL import Image

from winescan.catalog.images import inspect_image, packshot_score, phash_distance


def test_transparent_tall_packshot_scores_high(tmp_path):
    image = Image.new("RGBA", (200, 700), (0, 0, 0, 0))
    image.paste((120, 20, 40, 255), (60, 50, 140, 650))
    path = tmp_path / "bottle.png"
    image.save(path)

    info = inspect_image(path)

    assert info["error"] is None
    assert (info["width"], info["height"]) == (200, 700)
    assert info["transparent_share"] > 0.5
    assert packshot_score(info) == 3


def test_wide_noisy_photo_scores_low(tmp_path):
    rng = np.random.default_rng(0)
    path = tmp_path / "shelf.jpg"
    Image.fromarray(rng.integers(0, 255, (400, 700, 3), dtype=np.uint8)).save(path)

    assert packshot_score(inspect_image(path)) == 0


def test_broken_file_reports_error(tmp_path):
    path = tmp_path / "broken.webp"
    path.write_bytes(b"not an image")

    info = inspect_image(path)

    assert info["error"] and len(info["sha256"]) == 64
    assert packshot_score(info) == 0


def test_phash_distance():
    assert phash_distance("0" * 16, "f" * 16) == 64
    assert phash_distance("00000000000000ff", "000000000000000f") == 4
