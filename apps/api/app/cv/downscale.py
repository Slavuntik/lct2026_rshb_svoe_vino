"""Уменьшение входного кадра до заданной большей стороны — первый шаг `/v1/eval/predict`
и `/v1/scan/photo` (оба режима).

Кадр, у которого хотя бы одна сторона больше `max_side`, уменьшается пропорционально так,
чтобы большая сторона стала ровно `max_side`. Кадр не больше лимита отдаётся исходными
байтами без перекодирования.

Несгораемость: всё, что Pillow не смог прочитать (битый файл, конвенции mock-провайдера
`MOCKPHOTO:...`), возвращается как было — решение о том, что с такими байтами делать,
остаётся за движком, как и до этого шага.
"""

from __future__ import annotations

import io

SCAN_MAX_SIDE = 1024


def downscale_to_max_side(image_bytes: bytes, max_side: int = SCAN_MAX_SIDE) -> bytes:
    """JPEG-байты кадра с большей стороной `max_side`; кадр не больше лимита — исходные байты."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - зависит от окружения
        return image_bytes

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            # размер известен из заголовка, без декодирования пикселей
            width, height = image.size
            if max(width, height) <= max_side:
                return image_bytes
            scale = max_side / max(width, height)
            target = (max(1, round(width * scale)), max(1, round(height * scale)))
            # JPEG декодируется сразу с уменьшением в 2^k раз, но не меньше целевого размера —
            # полное декодирование 12-мегапиксельного кадра стоит сотни мс
            image.draft("RGB", target)
            # перекодированный JPEG уходит без метаданных — EXIF-ориентацию применяем здесь,
            # иначе бутылка с телефонного снимка «легла бы на бок»
            frame = ImageOps.exif_transpose(image).convert("RGB")
            if (frame.width > frame.height) != (width > height):
                target = (target[1], target[0])
            frame = frame.resize(target, Image.Resampling.LANCZOS)
    except Exception:  # PIL кидает разные типы на разный мусор
        return image_bytes

    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()
