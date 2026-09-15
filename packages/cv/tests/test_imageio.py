from __future__ import annotations

import numpy as np
import pytest

from cv.imageio import decode_image, encode_jpeg, letterbox_resize


def test_decode_image_rejects_empty_bytes():
    with pytest.raises(ValueError):
        decode_image(b"")


def test_decode_image_rejects_garbage_bytes():
    with pytest.raises(ValueError):
        decode_image(b"this is definitely not an image file \x00\x01\x02" * 10)


def test_decode_image_rejects_truncated_jpeg():
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 255, size=(64, 64, 3), dtype=np.uint8)
    full = encode_jpeg(arr)
    truncated = full[: len(full) // 3]
    with pytest.raises(ValueError):
        decode_image(truncated)


def test_encode_decode_roundtrip_preserves_shape():
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, size=(48, 32, 3), dtype=np.uint8)
    data = encode_jpeg(arr)
    back = decode_image(data)
    assert back.shape == arr.shape
    assert back.dtype == np.uint8


def test_letterbox_resize_shape_and_padding():
    img = np.full((100, 50, 3), 200, dtype=np.uint8)
    out = letterbox_resize(img, 64, pad_value=0)
    assert out.shape == (64, 64, 3)
    assert out[0, 0].tolist() == [0, 0, 0]  # угол — паддинг, не контент (узкое по ширине фото)
