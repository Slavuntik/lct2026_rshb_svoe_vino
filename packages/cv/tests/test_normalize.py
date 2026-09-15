from __future__ import annotations

import numpy as np

from cv.normalize import detect_label_region, normalize_illumination, normalize_query, unwarp_label


def test_detect_label_region_returns_ordered_quad_within_bounds(synthetic_bottle_image):
    h, w = synthetic_bottle_image.shape[:2]
    corners = detect_label_region(synthetic_bottle_image)
    assert corners.shape == (4, 2)
    assert (corners[:, 0] >= -1e-3).all() and (corners[:, 0] <= w + 1e-3).all()
    assert (corners[:, 1] >= -1e-3).all() and (corners[:, 1] <= h + 1e-3).all()
    tl, tr, br, bl = corners
    assert tl[0] < tr[0]  # top-left левее top-right
    assert tl[1] < bl[1]  # top-left выше bottom-left


def test_detect_label_region_never_raises_on_blank_image():
    blank = np.full((200, 100, 3), 127, dtype=np.uint8)  # нет контуров вообще
    corners = detect_label_region(blank)
    assert corners.shape == (4, 2)


def test_normalize_query_output_shape_and_dtype(synthetic_bottle_image):
    out = normalize_query(synthetic_bottle_image, enabled=True, out_size=224)
    assert out.shape == (224, 224, 3)
    assert out.dtype == np.uint8


def test_normalize_disabled_flag_skips_geometry(synthetic_bottle_image):
    """Флаг A/B: enabled=False -> детерминированный letterbox без детекта/warp'а,
    отличный по содержимому от enabled=True (иначе флаг ничего не делает)."""
    disabled = normalize_query(synthetic_bottle_image, enabled=False, out_size=224)
    enabled = normalize_query(synthetic_bottle_image, enabled=True, out_size=224)
    assert disabled.shape == enabled.shape == (224, 224, 3)
    assert not np.array_equal(disabled, enabled)

    # enabled=False детерминирован и НЕ зависит от detect_label_region
    disabled_again = normalize_query(synthetic_bottle_image, enabled=False, out_size=224)
    assert np.array_equal(disabled, disabled_again)


def test_unwarp_label_handles_degenerate_corners_gracefully():
    img = np.full((50, 50, 3), 100, dtype=np.uint8)
    corners = np.array([[10, 10], [10, 10], [10, 11], [10, 11]], dtype=np.float32)  # почти точка
    out = unwarp_label(img, corners, out_size=64)
    assert out.shape == (64, 64, 3)


def test_normalize_illumination_preserves_shape(synthetic_bottle_image):
    out = normalize_illumination(synthetic_bottle_image)
    assert out.shape == synthetic_bottle_image.shape
    assert out.dtype == np.uint8
