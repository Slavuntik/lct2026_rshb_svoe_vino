import numpy as np
import pytest
from PIL import Image

from winescan.vision.cylinder import rotate_cylinder, silhouette_rows


def _bottle_with_marker() -> Image.Image:
    """Цилиндр шириной 200 px с вертикальной чёрной линией по оси и прозрачным фоном."""
    image = np.zeros((300, 300, 4), dtype=np.uint8)
    image[:, 50:250] = (200, 180, 160, 255)
    image[:, 148:152, :3] = 0
    return Image.fromarray(image, "RGBA")


def _marker_column(image: Image.Image, row: int = 150) -> float:
    pixels = np.asarray(image)[row]
    dark = np.where((pixels[:, 3] > 0) & (pixels[:, :3].sum(axis=1) < 100))[0]
    return float(dark.mean())


def test_silhouette_rows():
    center, half = silhouette_rows(np.asarray(_bottle_with_marker())[..., 3], smooth=1)

    assert center[10] == pytest.approx(149.5) and half[10] == pytest.approx(99.5)


def test_zero_rotation_keeps_image():
    sprite = _bottle_with_marker()

    same = rotate_cylinder(sprite, yaw_deg=0)

    assert np.abs(np.asarray(same).astype(int) - np.asarray(sprite).astype(int)).mean() < 1.0


def test_rotation_moves_marker_by_radius_times_sine():
    rotated = rotate_cylinder(_bottle_with_marker(), yaw_deg=30)

    # линия на оси (угол 0) после поворота видна на угле 30°: сдвиг R·sin30° ≈ 50 px
    assert _marker_column(rotated) - 149.5 == pytest.approx(99.5 * 0.5, abs=3)


def test_silhouette_width_is_preserved_and_highlight_brightens():
    rotated = rotate_cylinder(_bottle_with_marker(), yaw_deg=20, shading=0.3, highlight_angle_deg=0)
    alpha = np.asarray(rotated)[..., 3]

    assert alpha[150, 60] > 0 and alpha[150, 40] == 0
    assert np.asarray(rotated)[150, 150, :3].mean() > 200  # блик по оси
