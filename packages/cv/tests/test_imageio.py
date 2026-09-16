from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image, ImageOps

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


# --- полевой приём (ревью 04, блокер 2): EXIF Orientation + HEIC --------------------


def _jpeg_with_orientation(orientation: int, size: tuple[int, int] = (40, 20)) -> bytes:
    """JPEG с маркером (красный квадрат в left-top ДО поворота) и заданным тегом
    EXIF Orientation — тот же рецепт, что живой зонд ревью 04 (телефон пишет поворот
    в EXIF, не в пиксели)."""
    img = Image.new("RGB", size, (0, 0, 0))
    px = img.load()
    for x in range(6):
        for y in range(6):
            px[x, y] = (255, 0, 0)
    exif = img.getexif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


@pytest.mark.parametrize("orientation", [1, 3, 6, 8])
def test_decode_image_applies_exif_orientation(orientation):
    """decode_image() обязан давать РОВНО то же, что `ImageOps.exif_transpose()` —
    не полагаемся на собственную интуицию о направлении поворота (легко перепутать
    знак), сверяем с эталонной реализацией Pillow напрямую."""
    data = _jpeg_with_orientation(orientation)
    decoded = decode_image(data)
    expected = np.asarray(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"))
    assert decoded.shape == expected.shape
    assert np.array_equal(decoded, expected)


def test_decode_image_exif_orientation_actually_changes_pixels():
    """Регресс-тест на сам баг ревью 04: без коррекции Orientation=6 (поворот на 90°)
    декодируется с ДРУГОЙ формой (H/W не переставлены) — если кто-то в будущем уберёт
    `exif_transpose`, этот тест упадёт первым, не дожидаясь просадки score на живом фото."""
    data = _jpeg_with_orientation(6, size=(40, 20))
    corrected = decode_image(data)
    naive = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    assert corrected.shape != naive.shape
    assert corrected.shape[:2] == (40, 20)  # повёрнуто: было 20×40 (H×W) в сырых пикселях


def test_decode_image_without_exif_is_noop():
    """Обычный PNG/скриншот без EXIF (в т.ч. синтетические ракурсы аугментатора,
    которые всегда JPEG без EXIF) проходит через exif_transpose без изменений."""
    arr = np.full((30, 50, 3), 128, dtype=np.uint8)
    data = encode_jpeg(arr)
    decoded = decode_image(data)
    assert decoded.shape == arr.shape


def test_decode_image_reads_heic():
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, size=(32, 48, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="HEIF")
    decoded = decode_image(buf.getvalue())
    assert decoded.shape == (32, 48, 3)
    assert decoded.dtype == np.uint8


def test_decode_image_rejects_truncated_heic():
    rng = np.random.default_rng(2)
    arr = rng.integers(0, 255, size=(32, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="HEIF")
    full = buf.getvalue()
    with pytest.raises(ValueError):
        decode_image(full[: len(full) // 2])
