"""Слой 2: чтение текста этикетки (EasyOCR, русский и английский).

Читаем только кроп упаковки: так быстрее и меньше мусора с ценников и соседних бутылок.
Результат — одна строка; разбор и сопоставление с каталогом — в winescan.search.text_match.
"""

from __future__ import annotations

import numpy as np
from PIL import Image


class LabelReader:
    def __init__(self, languages: tuple[str, ...] = ("ru", "en"), gpu: bool = True, max_side: int = 1280):
        import easyocr  # тяжёлый импорт: только когда OCR действительно нужен

        self.reader = easyocr.Reader(list(languages), gpu=gpu, verbose=False)
        self.max_side = max_side

    def read(self, image: Image.Image) -> str:
        image = image.convert("RGB")
        image.thumbnail((self.max_side, self.max_side))
        bgr = np.ascontiguousarray(np.asarray(image)[:, :, ::-1])  # EasyOCR ждёт порядок каналов OpenCV
        return " ".join(self.reader.readtext(bgr, detail=0, paragraph=False))
