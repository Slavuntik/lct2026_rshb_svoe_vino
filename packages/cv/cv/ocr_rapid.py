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

## Третий проход — КРОП ЭТИКЕТКИ при 1280 px, без новой детекции (agents/H3-label-
## crop-ocr.md, основание — `reports/label-crop-study.md`, `reports/h2-rapidocr.md`)

CPU-путь hack-v7: 91.9% top-1 на 62 живых фото (было 90.3%, 2 кропа CV). Разбор
промахов: детектор при 960px не находит МЕЛКИЕ строки этикетки («ПОЛУСЛАДКОЕ
РОЗОВОЕ», «WINEMAKER'S SELECTION») — перераспознавание из оригинала в native
разрешении хуже (88.7%), а третий проход детектор+распознаватель RapidOCR НА КРОПЕ
ЭТИКЕТКИ при 1280px (объединение с текстами 640+960 по кропу бутылки) даёт +1 фото.
При 1600px — 0 (замерено офлайн, `qa/real_photos_cpu_path.py --union
rapid_640+rapid_960+lab_rapid_1280`).

Кроп этикетки — БЕЗ отдельной модели-детектора: боксы детектора текста, УЖЕ
полученные на масштабе `max(self.sizes)` (по умолчанию 960 — см. `DEFAULT_RAPID_
SIZES`) внутри цикла `read()` ниже, кластеризуются в область этикетки
(`cv.label_crop.label_bbox()`, перенесён дословно из `qa/real_photos_label_crops.py`,
находит область на 100/100 живых фото каталога). Область вырезается ИЗ ПОЛНОГО
РАЗРЕШЕНИЯ кропа бутылки (аргумент `read()`, ДО какого-либо даунскейла — тот же смысл,
что "оригинал" в прототипе: `im.crop(box)`, а не `small.crop()`), затем ресайзится
(см. "Бикубика" ниже) до `CV_OCR_LABEL_SIZE` и читается СВОИМ, третьим, экземпляром
движка (кэшируется в `_engines[label_size]` — тот же принцип "один движок на
масштаб", что и `self.sizes`). Этикетка не найдена (`label_bbox()` вернул `None`) —
проход ПРОПУСКАЕТСЯ (не читается вовсе, не подставляется весь кроп бутылки заново —
это отдельный эксперимент, измерен, пользы не даёт, см. `cv.label_crop` докстринг).

`CV_OCR_LABEL_SIZE` (дефолт 1280, `0` — выключено) читается ЖИВЬЁМ ЗДЕСЬ, в
`RapidOcrReader.__init__` (НЕ в `cv.verify.LabelVerifier`, как `CV_OCR_RAPID_SIZES`/
`CV_OCR_ENGINE`) — зона записи этой волны (`agents/H3-label-crop-ocr.md`) ограничена
файлами `cv/ocr_rapid.py`/`cv/label_crop.py`, `cv/verify.py` НЕ трогается: конструктор
`RapidOcrReader(sizes=self.rapid_sizes)` в `LabelVerifier._rapid_reader()` не меняется,
третий масштаб настраивается ИЗНУТРИ этого модуля.

Движок `label_size` конструируется/греется В `read()` ВСЕГДА, когда `CV_OCR_LABEL_
SIZE > 0` — даже если `label_bbox()` не находит область на конкретном кадре (например,
на 2×2-плейсхолдере `warm_up_label_verifier()`): та же дисциплина прогрева, что и у
`self.sizes` выше (`_engine_for(size)` вызывается на КАЖДОМ `read()` независимо от
того, нашёлся ли текст) — первый боевой запрос с настоящей этикеткой не должен платить
холодный старт третьего движка.

## Бикубика для МЕЛКИХ входов — `_resize_to_longest_side()` (agents/H3-label-crop-
## ocr.md, задача 3)

`_downscale_to_longest_side()` выше (H2) осознанно НИКОГДА не увеличивает — это
доказанно совпадает с оффлайн-кэшем прототипа для НОРМАЛЬНЫХ фото. Но на фото с
короткой стороной < ~1000px кроп бутылки (`CENTER_CROP`, доли 0.15/0.05/0.85/0.98)
уже МЕНЬШЕ 960 — детектор/распознаватель видят МЕНЬШЕ пикселей на строку текста, чем
на нормальном фото, именно там, где мелкий текст УЖЕ на грани разрешимости (см. раздел
выше). `_resize_to_longest_side()` — новая функция (используется ПЕРЕД КАЖДЫМ
проходом — всеми `self.sizes` и третьим `label_size` — не только последним): когда
кроп МЕНЬШЕ целевой стороны, увеличивает через `PIL.Image.resize(...,
Image.Resampling.BICUBIC)` (аспект сохраняется, целевая сторона — длинная); когда
БОЛЬШЕ или РАВНА — не переизобретает, делегирует `_downscale_to_longest_side()` как
есть (H2-инвариант и его тесты не тронуты). Обе ветки применяются одинаково к ЛЮБОМУ
входу фиксированного размера — не только к "мелким фото", это СВОЙСТВО кропа
относительно `target` каждого конкретного прохода (640/960/1280 могут по-разному
решить "больше или меньше" для одного и того же кропа).
"""
from __future__ import annotations

import os
import warnings
from typing import Any

import numpy as np
from PIL import Image

from cv.label_crop import label_bbox  # agents/H3-label-crop-ocr.md — см. докстринг модуля, "Третий проход"
from cv.verify import _center_crop  # доли CENTER_CROP — см. cv.verify.CENTER_CROP, докстринг модуля выше

DEFAULT_RAPID_SIZES: tuple[int, ...] = (640, 960)
DEFAULT_SCORE_THRESH = 0.5
# agents/H3-label-crop-ocr.md: CV_OCR_LABEL_SIZE — третий масштаб, детектор+
# распознаватель на КРОПЕ ЭТИКЕТКИ (см. докстринг модуля, "Третий проход"). 0 = выключено.
DEFAULT_LABEL_SIZE = 1280
# 22.09 (оркестратор, разбор промахов стенда hack-v8): пороги ДЕТЕКТОРА текста. Дефолты движка
# (box_thresh 0.6, unclip_ratio 1.5) не собирают строки, набранные ВРАЗРЯДКУ мелкими капителями
# («ПОЛУСЛАДКОЕ РОЗОВОЕ» на «Новом Свете», «WINEMAKER'S SELECTION» на золотой ленте Инкермана):
# каждая буква — отдельный слабый бокс ниже порога, и строка теряется целиком. При box_thresh 0.3 и
# unclip_ratio 2.0 обе читаются; на 62 живых фото CPU-путь 56 → 59 (90.3 → 95.2% top-1), объединение
# 640+960; лишний шум гейт «винодельня» и IDF переваривают. Env: CV_OCR_DET_BOX_THRESH, CV_OCR_DET_UNCLIP.
DEFAULT_DET_BOX_THRESH = 0.3
DEFAULT_DET_UNCLIP = 2.0


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


def _resize_to_longest_side(image_arr: np.ndarray, target: int) -> np.ndarray:
    """Меняет размер по ДЛИННОЙ стороне до `target`, сохраняя пропорции — В ОБЕ
    СТОРОНЫ (agents/H3-label-crop-ocr.md, задача 3 — см. докстринг модуля,
    "Бикубика для МЕЛКИХ входов"). Кроп БОЛЬШЕ или РАВЕН `target` — без изменений
    в этой функции, делегирует `_downscale_to_longest_side()` (H2-инвариант,
    её тесты не тронуты этой правкой). Кроп МЕНЬШЕ `target` — увеличивает через
    `PIL.Image.resize(..., Image.Resampling.BICUBIC)` (та же дисциплина округления
    "новая сторона = round(other * target/max(h,w))", что `cv.verify.LabelVerifier.
    read_text()` уже использует для `cv2.resize()` перед PaddleOCR — просто в
    сторону увеличения, а не уменьшения)."""
    h, w = image_arr.shape[:2]
    if max(h, w) >= target:
        return _downscale_to_longest_side(image_arr, target)
    scale = target / max(h, w)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    im = Image.fromarray(image_arr, mode="RGB")
    im = im.resize((new_w, new_h), Image.Resampling.BICUBIC)
    return np.asarray(im)


class RapidOcrReader:
    """Читает текст этикетки через RapidOCR в НЕСКОЛЬКО масштабов и объединяет тексты
    пробелом (docstring модуля, "Основание"). Ленивая загрузка: конструктор и импорт
    модуля не тянут rapidocr/onnxruntime/веса моделей, пока движок реально не
    понадобился — тот же принцип, что `cv.encoder.SiglipEncoder`/`cv.verify.
    LabelVerifier`. Один экземпляр `RapidOCR` НА МАСШТАБ (см. docstring модуля), созданный
    по требованию и закэшированный на self — повторные вызовы `read()` не пересоздают
    движки.

    `label_size` (agents/H3-label-crop-ocr.md, докстринг модуля "Третий проход") —
    третий, независимый масштаб: НЕ входит в `self.sizes`, читается ОТДЕЛЬНО внутри
    `read()` поверх боксов, уже найденных на `max(self.sizes)`. `None` (дефолт) читает
    `CV_OCR_LABEL_SIZE` ЖИВЬЁМ здесь же (env, не аргумент конструктора — тот же приём,
    что `cv.verify.LabelVerifier` уже использует для СВОИХ env-переменных, но здесь, а
    не там, см. докстринг модуля "Третий проход" про зону записи этой волны) — так тест
    может `monkeypatch.setenv("CV_OCR_LABEL_SIZE", ...)` ДО конструктора. Явный `0`
    (аргументом ИЛИ через env) выключает третий проход целиком."""

    def __init__(
        self,
        sizes: tuple[int, ...] | None = None,
        score_thresh: float = DEFAULT_SCORE_THRESH,
        label_size: int | None = None,
    ):
        self.sizes: tuple[int, ...] = tuple(sizes) if sizes else DEFAULT_RAPID_SIZES
        self.score_thresh = score_thresh
        raw_label_size = os.environ.get("CV_OCR_LABEL_SIZE")
        if raw_label_size is not None:
            self.label_size = int(raw_label_size.strip())
        elif label_size is not None:
            self.label_size = int(label_size)
        else:
            self.label_size = DEFAULT_LABEL_SIZE
        self.det_box_thresh = float(os.environ.get("CV_OCR_DET_BOX_THRESH", DEFAULT_DET_BOX_THRESH))
        self.det_unclip = float(os.environ.get("CV_OCR_DET_UNCLIP", DEFAULT_DET_UNCLIP))
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
                    # Container packages are read-only; keep downloaded weights in a
                    # writable, persistent cache when explicitly configured.
                    **({"Global.model_root_dir": os.environ["CV_OCR_RAPID_MODELS_DIR"]}
                       if os.environ.get("CV_OCR_RAPID_MODELS_DIR") else {}),
                    "Det.limit_side_len": size,
                    "Det.limit_type": "max",
                    "Det.box_thresh": self.det_box_thresh,
                    "Det.unclip_ratio": self.det_unclip,
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

    def _run_engine(self, engine: Any, size: int, image_arr: np.ndarray) -> Any | None:
        """Вызывает движок конкретного масштаба, возвращает `RapidOCROutput` (сырой
        результат, включая `.boxes` — нужны третьему проходу, см. `_read_label_pass()`)
        или `None` на сбое ВЫЗОВА (не импорта/конструктора — те уже разобраны
        `_engine_for()`) — деградация, не исключение, та же дисциплина, что раньше
        была внутри `_read_one_scale()` (H2), просто без немедленного извлечения
        текста — вынесено отдельно, чтобы боксы масштаба-источника этикетки (`read()`
        ниже) можно было переиспользовать, а не только его текст."""
        try:
            return engine(image_arr)
        except Exception as exc:  # noqa: BLE001 — сбой движка НА ЭТОМ вызове = деградация, не 500
            warnings.warn(f"cv.ocr_rapid: сбой распознавания {size}px ({exc!r})", stacklevel=2)
            return None

    def _extract_text(self, result: Any | None) -> str:
        """`RapidOCROutput` (или `None`) -> текст (строки объединены пробелом), только
        куски с score >= `self.score_thresh` (H2, докстринг модуля "Основание",
        логика бит-в-бит как была в `_read_one_scale()` до разделения на вызов
        движка/извлечение текста)."""
        # rapidocr.utils.output.RapidOCROutput: .txts/.scores — Optional[Tuple[...]], та
        # же дисциплина порога, что и прототип (qa/real_photos_rapidocr.py, дословно):
        # без scores совпадающей длины ни один текст не считается прочитанным.
        txts = result.txts if result is not None else None
        if not txts:
            return ""
        scores = result.scores or ()
        kept = [t for t, s in zip(txts, scores) if s >= self.score_thresh]
        return " ".join(kept)

    def _read_label_pass(
        self,
        bottle_full: np.ndarray,
        boxes: Any | None,
        scores: Any | None,
        source_hw: tuple[int, int] | None,
    ) -> str:
        """Третий проход — детектор+распознаватель на КРОПЕ ЭТИКЕТКИ при
        `self.label_size` (agents/H3-label-crop-ocr.md, докстринг модуля "Третий
        проход"). `boxes`/`scores`/`source_hw` — боксы и ИХ форма кадра (H, W),
        уже полученные `read()` на масштабе-источнике (`max(self.sizes)`), НЕ новая
        детекция.

        Движок `self.label_size` конструируется/греется ЗДЕСЬ ВСЕГДА (см. докстринг
        класса) — даже когда боксов нет вовсе, до проверки `label_bbox()`.

        `label_bbox()` (`cv.label_crop`) кластеризует боксы в координатах
        МАСШТАБИРОВАННОГО кадра-источника (`source_hw`); `None` (этикетка не
        найдена — нет кандидатов после фильтра скора/центра/высоты) -> пустая
        строка, проход ПРОПУСКАЕТСЯ (не заменяется чтением всего кропа бутылки —
        см. `cv.label_crop`, докстринг модуля). Иначе — бокс масштабируется
        ОБРАТНО к разрешению `bottle_full` (та же арифметика, что прототип:
        `k = crop.size[0] / small.size[0]`, отношение ШИРИН) и вырезается ИЗ
        `bottle_full` (полное разрешение кропа бутылки, ДО даунскейла на
        масштаб-источник — "оригинал" в терминах прототипа)."""
        engine = self._engine_for(self.label_size)
        if engine is None:
            return ""
        # `boxes`/`scores` — НАСТОЯЩИЙ RapidOCROutput.boxes ЖИВЬЁМ приходит numpy
        # ndarray (N,4,2) (`scores` — тюпл), НЕ список: `not boxes`/`x or default`
        # на ndarray с >1 элементом падает "The truth value of an array with more
        # than one element is ambiguous" (найдено приёмкой живым API 22.09, см.
        # reports/h3-label-crop-ocr.md) — только `is None`/`len(...)` безопасны.
        if boxes is None or len(boxes) == 0 or not source_hw:
            return ""
        src_h, src_w = source_hw
        bb = label_bbox(boxes, scores if scores is not None else (), src_w, src_h)
        if bb is None:
            return ""
        full_h, full_w = bottle_full.shape[:2]
        k = full_w / src_w if src_w else 1.0  # прототип: k = crop.size[0] / small.size[0] (ширина)
        # int(...) — усечение, НЕ round(): та же арифметика, что прототип
        # (qa/real_photos_label_crops.py::main, box = (int(cx0 + bb[0]*k), ...)).
        x0 = max(0, int(bb[0] * k))
        y0 = max(0, int(bb[1] * k))
        x1 = min(full_w, int(bb[2] * k))
        y1 = min(full_h, int(bb[3] * k))
        if x1 <= x0 or y1 <= y0:
            return ""
        label_crop = np.ascontiguousarray(bottle_full[y0:y1, x0:x1])
        scaled_label = np.ascontiguousarray(_resize_to_longest_side(label_crop, self.label_size))
        result = self._run_engine(engine, self.label_size, scaled_label)
        return self._extract_text(result)

    def read(self, image_arr: np.ndarray) -> str:
        """RGB ndarray (уже кроп нужного региона, любой размер) -> тексты ВСЕХ
        `self.sizes` плюс третий проход по кропу этикетки (`self.label_size`, см.
        докстринг модуля "Третий проход") через пробел, только куски с score >=
        `self.score_thresh`. Каждый масштаб СНАЧАЛА ресайзится к своему `size` по
        длинной стороне (`_resize_to_longest_side()` — уменьшает ИЛИ увеличивает,
        докстринг модуля "Бикубика для МЕЛКИХ входов"; до agents/H3-label-crop-
        ocr.md здесь был `_downscale_to_longest_side()`, только уменьшавший) — так
        `size` реально управляет разрешением, которое видит распознаватель, а не
        только внутренним ресайзом детектора. Сбой одного масштаба не роняет
        остальные — конкатенация того, что реально распозналось (пустая строка,
        если отказали все масштабы и третий проход).

        Боксы масштаба `max(self.sizes)` (если он отработал) запоминаются для
        третьего прохода — БЕЗ дополнительной детекции, см. `_read_label_pass()`."""
        arr = np.ascontiguousarray(image_arr)
        parts: list[str] = []
        # label_source_scale вычисляется, только когда третий проход вообще включён —
        # НЕ трогаем result.boxes вовсе при CV_OCR_LABEL_SIZE=0 (не только ленивость:
        # RapidOCROutput настоящего движка всегда несёт .boxes, но объекты, которые
        # read()/read_center() получают в тестах/от иных источников result, могут его
        # не нести — трогать атрибут стоит, только когда он реально нужен).
        label_source_scale = max(self.sizes) if self.sizes and self.label_size > 0 else None
        label_boxes: Any | None = None
        label_scores: Any | None = None
        label_source_hw: tuple[int, int] | None = None
        for size in self.sizes:
            engine = self._engine_for(size)
            if engine is None:
                continue
            scaled = np.ascontiguousarray(_resize_to_longest_side(arr, size))
            result = self._run_engine(engine, size, scaled)
            text = self._extract_text(result)
            if text:
                parts.append(text)
            if size == label_source_scale and result is not None:
                label_boxes, label_scores = result.boxes, result.scores
                label_source_hw = scaled.shape[:2]
        if self.label_size > 0:
            label_text = self._read_label_pass(arr, label_boxes, label_scores, label_source_hw)
            if label_text:
                parts.append(label_text)
        return " ".join(parts)

    def read_center(self, full_frame: np.ndarray) -> str:
        """Центральный кроп ПОЛНОГО кадра запроса (`cv.verify.CENTER_CROP`/
        `_center_crop()` — те же доли, что H1) -> `read()`. Единственный путь чтения
        этого движка (docstring модуля, "Центральный кроп") — в отличие от
        `cv.verify.LabelVerifier`, здесь нет отдельного детекторного режима."""
        return self.read(_center_crop(full_frame))
