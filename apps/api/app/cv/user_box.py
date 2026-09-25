"""Рамка, которой пользователь сам указывает бутылку в кадре (v0.4.10).

Зачем. Выбор нужной бутылки — самое узкое место кадра «у полки»: замер второго движка
показал потолок автоматического выбора (0,775 при идеальном выборе среди рамок детектора
против 0,663 при автоматическом), и из самого кадра признак «та самая бутылка» не
извлекается. Прицел в интерфейсе снимает эту неопределённость целиком: пользователь
показывает пальцем, а сервис больше не гадает.

Как встроено. Рамка применяется **до** движка: кадр вырезается здесь, а `ImageIndex.search`
получает уже готовые байты. Поэтому контракт движка не меняется и фича работает у обоих
провайдеров (`real` и `winescan`), а не только у того, чей пайплайн умеет принимать рамку.

Формат поля — `x1,y1,x2,y2` в долях кадра (0…1), слева сверху. Доли, а не пиксели: клиент
показывает фотографию вписанной (`object-fit: contain`) и не знает, в каком разрешении она
уйдёт на сервер после сжатия камерой.
"""

from __future__ import annotations

import io

# рамка меньше этой доли кадра по стороне — почти наверняка промах пальцем, а не бутылка
MIN_SIDE_FRACTION = 0.02


def parse_box(raw: str) -> tuple[float, float, float, float]:
    """`"0.1,0.2,0.6,0.9"` -> доли кадра. ValueError с понятным текстом на всё остальное."""
    parts = [piece.strip() for piece in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("ожидались четыре числа «x1,y1,x2,y2» в долях кадра")
    try:
        x1, y1, x2, y2 = (float(piece) for piece in parts)
    except ValueError as error:
        raise ValueError(f"рамка должна состоять из чисел: {error}") from error
    if not all(0.0 <= value <= 1.0 for value in (x1, y1, x2, y2)):
        raise ValueError("доли рамки выходят за пределы кадра (ожидается 0…1)")
    if x2 - x1 < MIN_SIDE_FRACTION or y2 - y1 < MIN_SIDE_FRACTION:
        raise ValueError("рамка вырождена: стороны меньше 2% кадра")
    return x1, y1, x2, y2


def crop_to_box(image_bytes: bytes, box: tuple[float, float, float, float]) -> bytes:
    """Вырезает указанную часть кадра. Возвращает JPEG-байты — их и получит движок.

    Формат намеренно один и тот же независимо от входного: движки принимают байты, а не
    объект картинки, и лишняя ветка «отдать как было, если это уже JPEG» экономила бы
    миллисекунды ценой второго пути, который никто не тестирует.
    """
    # Pillow импортируется здесь, а не наверху: без рамки сервис работает и без него, и
    # отсутствие необязательной зависимости не должно ронять весь роутер скана.
    try:
        from PIL import Image, ImageOps
    except ImportError as error:  # pragma: no cover - зависит от окружения
        raise ValueError(f"рамка требует Pillow, а он не установлен: {error}") from error

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            frame = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as error:  # PIL кидает разные типы на разный мусор
        raise ValueError(f"не удалось прочитать изображение: {error}") from error

    x1, y1, x2, y2 = box
    width, height = frame.size
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    # после округления рамка может схлопнуться на маленьком кадре — расширяем до одного пикселя
    right, bottom = max(right, left + 1), max(bottom, top + 1)

    buffer = io.BytesIO()
    frame.crop((left, top, right, bottom)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def apply_user_box(image_bytes: bytes, raw_box: str | None) -> bytes:
    """Байты кадра с учётом рамки пользователя; без рамки — исходные байты без копирования."""
    if raw_box is None or not raw_box.strip():
        return image_bytes
    return crop_to_box(image_bytes, parse_box(raw_box))
