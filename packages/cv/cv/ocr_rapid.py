"""cv/ocr_rapid.py — RapidOCR (ONNX Runtime) в НЕСКОЛЬКО масштабов, быстрый источник
текста CPU-пути слияния (agents/H2-rapidocr-multiscale.md).

## Основание (reports/cpu-path-study.md, оркестратор, офлайн на 62 живых фото каталога)

RapidOCR (детектор PP-OCRv5 mobile, распознаватель PP-OCRv5 eslav mobile) читает
центральный кроп бутылки 640px за **0.34 с против 4.0 с** у PaddleOCR на том же слабом
CPU (ams3, 4 vCPU) — не зависит от бага oneDNN paddlepaddle 3.3.1 (см. `cv.verify`,
`somelye.env.example`). Один масштаб RapidOCR по точности ≈ PaddleOCR (84–89% top-1), но
**объединение текстов ДВУХ масштабов (640 + 960 px)** — главный рычаг: мелкие слова
этикетки (винодельня, сорт) читаются на 960, крупные стилизованные буквы — на 640.
Замерено `qa/real_photos_rapidocr.py` + `qa/real_photos_cpu_path.py`: 90.3% (2 кропа CV)
и 93.5% (8 кропов CV) top-1 против 85.5% боевого H1-пути (PaddleOCR).

## Параметры движка — ДОСЛОВНО из прототипа (`qa/real_photos_rapidocr.py`)

`Det.model_type=ModelType.MOBILE` (не server — 2.0 с и не лучше по точности на слабом
CPU), `Det.ocr_version=OCRVersion.PPOCRV5`, `Det.limit_type="max"` (НЕ дефолтный "min"
библиотеки — ограничивает ДЛИННУЮ сторону кадра целевым `size`, что и нужно после
центрального кропа), `Global.use_cls=False` (ориентация текста уже решена EXIF-
транспонированием на входе, см. `cv.imageio.decode_image`), `Rec.lang_type=
LangRec.ESLAV` (кириллица+латиница одним распознавателем — источник гомоглифов, которые
`cv.text_fusion.homoglyph_variant()` умеет разворачивать обратно), порог скора 0.5.

## Один движок НА МАСШТАБ, не один движок с параметром на вызов

Проверено эмпирически (`rapidocr.RapidOCR.__call__()`, rapidocr==3.9.2): вызов
принимает только `use_det/use_cls/use_rec/return_word_box/return_single_char_box/
text_score/box_thresh/unclip_ratio` — `Det.limit_side_len` НЕ входит в этот список,
это параметр КОНСТРУКТОРА (переключает ЗАГРУЖЕННУЮ конфигурацию детектора, не разовое
поведение одного вызова). Поэтому каждый масштаб `CV_OCR_RAPID_SIZES` — свой отдельный
`RapidOCR(params=...)`, как и в прототипе (тот создаёт новый экземпляр в цикле по
`--sizes`), не один движок с "параметром на вызов".

## Явный даунскейл ДО вызова движка — не только `Det.limit_side_len`

`Det.limit_side_len`/`limit_type=max` управляют ТОЛЬКО внутренним ресайзом ДЕТЕКТОРА
(`rapidocr.main.RapidOCR.text_det`) — само распознавание вырезает регионы из кадра,
прошедшего лишь ГЛОБАЛЬНЫЙ препроцессинг движка (`Global.max_side_len`, дефолт 2000px,
`rapidocr.utils.process_img.resize_image_within_bounds()`, проверено по исходнику
rapidocr==3.9.2), а не из внутреннего входа детектора. Без явного даунскейла ДО вызова
(как делает прототип `im.thumbnail((size, size))` ПЕРЕД `eng(...)`) распознаватель видел
бы кроп разрешением вплоть до 2000px НЕЗАВИСИМО от `size` — двухмасштабный трюк
640/960 (см. "Основание" выше) выродился бы в "один и тот же вход дважды" (проверено
эмпирически на живом фото 48.98_02-09-2026_18-34-15.webp: без даунскейла оба масштаба
дали побитово одинаковый текст, хотя кэш прототипа показывает разный — 640px "Py6uH",
960px "Py6iH").

`_downscale_to_longest_side()` ниже воспроизводит РЕЗУЛЬТАТ прототипа (`PIL.Image.
thumbnail()`, `Image.Resampling.BICUBIC` + `reducing_gap=2.0` — дефолты Pillow==12.3.0),
а НЕ `cv2.resize(..., INTER_AREA)`, которым `cv.verify.LabelVerifier.read_text()` уже
даунскейлит перед PaddleOCR: сравнено эмпирически на 4 фото приёмки живым API (21.09,
agents/H2-rapidocr-multiscale.md задача 7) — `cv2.INTER_AREA` даёт МАКС. расхождение
пикселей 18-27/255 против PIL-пути и на границе читаемости ТЕРЯЕТ распознанные слова,
которых прототип (и, соответственно, офлайн-оценка 90.3%/93.5%) читает: «RUБИН
ГОЛОДРИГИ» (94.02_24-08-2026_17-46-57.webp) и «Py6iH» (96.96_05-09-2026_19-02-01.webp)
пропадают целиком при INTER_AREA, но появляются побитово идентично кэшу прототипа при
PIL-ресайзе. Разные фильтры интерполяции (BICUBIC у PIL, area-усреднение у INTER_AREA)
по-разному сглаживают тонкие штрихи мелкого текста — для CV/детектора-этикетки эта
разница не критична (потому `LabelVerifier` её не замечал), но для распознавания
МЕЛКИХ печатных слов на границе разрешимости — критична. НЕ меняет `cv.verify.py`
(вне зоны этой находки, другой движок/история валидации) — только `cv.ocr_rapid.py`,
чтобы совпадать с фактическим офлайн-замером ЭТОГО движка.

## Центральный кроп — тот же, что `cv.verify` (H1), без отдельного режима

RapidOCR-путь всегда читает ЦЕНТРАЛЬНЫЙ кроп ПОЛНОГО кадра запроса
(`cv.verify.CENTER_CROP`/`_center_crop()` — доли 0.15/0.05/0.85/0.98, те же, что видят
VLM и офлайн-эксперимент), НЕ детектор этикетки `cv.normalize.detect_label_region`.
Брифа п.2: отдельный режим `CV_OCR_QUERY_MODE` (H1, "detector"|"center") для rapid не
нужен — быстрый движок не имеет офлайн-обоснования для форка на детекторный кроп (H1
изучал это только для PaddleOCR); `RapidOcrReader.read_center()` всегда центр.

## Деградация — усилитель, не обязательный компонент

Сбой ИМПОРТА `rapidocr`/`onnxruntime` (пакет не установлен) ИЛИ сбой КОНСТРУКТОРА
движка (например, сеть недоступна для первого скачивания моделей) ИЛИ сбой вызова на
конкретном фото — во всех трёх случаях: warning в stderr + пустая строка для
пострадавшего масштаба, не исключение и не 500 (та же дисциплина деградации, что
`cv.verify.LabelVerifier.read_text()` уже применяет к PaddleOCR). Один масштаб может
отказать, а другой — сработать: конкатенация того, что реально распозналось.
"""
from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from PIL import Image

from cv.verify import _center_crop  # доли CENTER_CROP — см. cv.verify.CENTER_CROP, докстринг модуля выше

DEFAULT_RAPID_SIZES: tuple[int, ...] = (640, 960)
DEFAULT_SCORE_THRESH = 0.5


def parse_sizes(raw: str) -> tuple[int, ...]:
    """"640,960" -> (640, 960). Пробелы вокруг чисел допустимы ("640, 960").
    Пустая строка/строка без чисел -> ValueError (явная ошибка конфигурации при
    старте лучше, чем молчаливый пустой список масштабов на каждом запросе)."""
    sizes = tuple(int(s.strip()) for s in raw.split(",") if s.strip())
    if not sizes:
        raise ValueError(f"cv.ocr_rapid.parse_sizes: пустой список масштабов ({raw!r})")
    return sizes


def _downscale_to_longest_side(image_arr: np.ndarray, target: int) -> np.ndarray:
    """Уменьшает (НИКОГДА не увеличивает) до `target` по ДЛИННОЙ стороне, сохраняя
    пропорции — через `PIL.Image.thumbnail()`, БИТ-В-БИТ то же, что делает прототип
    (`qa/real_photos_rapidocr.py`) ПЕРЕД вызовом движка. НЕ `cv2.resize(...,
    INTER_AREA)` (тот приём `cv.verify.LabelVerifier.read_text()` использует для
    PaddleOCR) — см. докстринг модуля, "Явный даунскейл ДО вызова движка": разные
    фильтры интерполяции меряно дают разный текст на границе разрешимости мелких
    слов, а офлайн-оценка 90.3%/93.5% (agents/H2-rapidocr-multiscale.md) посчитана
    именно на PIL-пути прототипа."""
    h, w = image_arr.shape[:2]
    if max(h, w) <= target:
        return image_arr
    im = Image.fromarray(image_arr, mode="RGB")
    im.thumbnail((target, target))
    return np.asarray(im)


class RapidOcrReader:
    """Читает текст этикетки через RapidOCR в НЕСКОЛЬКО масштабов и объединяет тексты
    пробелом (docstring модуля, "Основание"). Ленивая загрузка: конструктор и импорт
    модуля не тянут rapidocr/onnxruntime/веса моделей, пока движок реально не
    понадобился — тот же принцип, что `cv.encoder.SiglipEncoder`/`cv.verify.
    LabelVerifier`. Один экземпляр `RapidOCR` НА МАСШТАБ (см. docstring модуля), созданный
    по требованию и закэшированный на self — повторные вызовы `read()` не пересоздают
    движки."""

    def __init__(self, sizes: tuple[int, ...] | None = None, score_thresh: float = DEFAULT_SCORE_THRESH):
        self.sizes: tuple[int, ...] = tuple(sizes) if sizes else DEFAULT_RAPID_SIZES
        self.score_thresh = score_thresh
        self._engines: dict[int, Any] = {}  # масштаб -> RapidOCR, создаётся по требованию
        self._unavailable_sizes: set[int] = set()  # масштабы, чей движок УЖЕ не удалось создать в этом процессе

    def _engine_for(self, size: int) -> Any | None:
        """Движок для конкретного масштаба или `None`, если создать не удалось
        (импорт/конструктор упали — warning уже выдан, дальше не повторяем попытку
        для ЭТОГО масштаба в рамках жизни объекта, см. `_unavailable_sizes`)."""
        if size in self._engines:
            return self._engines[size]
        if size in self._unavailable_sizes:
            return None
        try:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        except Exception as exc:  # noqa: BLE001 — деградация: пакет не установлен/сломан
            warnings.warn(
                f"cv.ocr_rapid: импорт rapidocr не удался ({exc!r}) — текст RapidOCR {size}px пуст",
                stacklevel=2,
            )
            self._unavailable_sizes.add(size)
            return None
        try:
            engine = RapidOCR(
                params={
                    "Global.use_cls": False,
                    "Det.limit_side_len": size,
                    "Det.limit_type": "max",
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.MOBILE,
                    "Rec.lang_type": LangRec.ESLAV,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                }
            )
        except Exception as exc:  # noqa: BLE001 — деградация: сеть/диск недоступны при первой загрузке модели
            warnings.warn(
                f"cv.ocr_rapid: не удалось создать движок RapidOCR {size}px ({exc!r}) — текст пуст",
                stacklevel=2,
            )
            self._unavailable_sizes.add(size)
            return None
        self._engines[size] = engine
        return engine

    def _read_one_scale(self, engine: Any, size: int, image_arr: np.ndarray) -> str:
        try:
            result = engine(image_arr)
        except Exception as exc:  # noqa: BLE001 — сбой движка НА ЭТОМ вызове = деградация, не 500
            warnings.warn(f"cv.ocr_rapid: сбой распознавания {size}px ({exc!r})", stacklevel=2)
            return ""
        # rapidocr.utils.output.RapidOCROutput: .txts/.scores — Optional[Tuple[...]], та
        # же дисциплина порога, что и прототип (qa/real_photos_rapidocr.py, дословно):
        # без scores совпадающей длины ни один текст не считается прочитанным.
        txts = result.txts if result is not None else None
        if not txts:
            return ""
        scores = result.scores or ()
        kept = [t for t, s in zip(txts, scores) if s >= self.score_thresh]
        return " ".join(kept)

    def read(self, image_arr: np.ndarray) -> str:
        """RGB ndarray (уже кроп нужного региона, любой размер) -> тексты ВСЕХ
        `self.sizes` через пробел, только куски с score >= `self.score_thresh`.
        Каждый масштаб СНАЧАЛА даунскейлится к своему `size` по длинной стороне
        (`_downscale_to_longest_side()`, докстринг модуля "Явный даунскейл ДО
        вызова движка") — так `size` реально управляет разрешением, которое видит
        распознаватель, а не только внутренним ресайзом детектора. Сбой одного
        масштаба не роняет остальные — конкатенация того, что реально распозналось
        (пустая строка, если отказали все масштабы)."""
        arr = np.ascontiguousarray(image_arr)
        parts: list[str] = []
        for size in self.sizes:
            engine = self._engine_for(size)
            if engine is None:
                continue
            scaled = np.ascontiguousarray(_downscale_to_longest_side(arr, size))
            text = self._read_one_scale(engine, size, scaled)
            if text:
                parts.append(text)
        return " ".join(parts)

    def read_center(self, full_frame: np.ndarray) -> str:
        """Центральный кроп ПОЛНОГО кадра запроса (`cv.verify.CENTER_CROP`/
        `_center_crop()` — те же доли, что H1) -> `read()`. Единственный путь чтения
        этого движка (docstring модуля, "Центральный кроп") — в отличие от
        `cv.verify.LabelVerifier`, здесь нет отдельного детекторного режима."""
        return self.read(_center_crop(full_frame))
