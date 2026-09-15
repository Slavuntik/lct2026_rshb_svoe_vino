import numpy as np
from PIL import Image

from winescan.search.build_index import load_reference_views


def _save(tmp_path, width, height, name):
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[:, width // 4 : 3 * width // 4] = (150, 30, 40, 255)
    image[height // 2 : height // 2 + 20, width // 4 : 3 * width // 4, :3] = 255
    path = tmp_path / name
    Image.fromarray(image, "RGBA").save(path)
    return path


def test_tall_bottle_gets_one_view_per_yaw(tmp_path):
    bottle = _save(tmp_path, 200, 800, "bottle.png")

    views = load_reference_views(bottle, "label", (-30.0, 0.0, 30.0))

    assert len(views) == 3
    assert all(v.size[0] == v.size[1] for v in views)
    assert not np.array_equal(np.asarray(views[0]), np.asarray(views[2]))


def test_box_package_is_not_rotated(tmp_path):
    box = _save(tmp_path, 600, 600, "box.png")

    assert len(load_reference_views(box, "full", (-30.0, 0.0, 30.0))) == 1


def test_frontal_only_matches_previous_single_view(tmp_path):
    bottle = _save(tmp_path, 200, 800, "bottle.png")

    assert len(load_reference_views(bottle, "full", (0.0,))) == 1
