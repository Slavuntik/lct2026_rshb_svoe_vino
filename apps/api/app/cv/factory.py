"""Фабрики ImageIndex/LabelVerifier по env (симметрично rag/factory.py и
packages/llm.get_llm()).

IMAGE_PROVIDER не задан или "mock" => MockImageIndex (фикстуры, без сети/моделей).
IMAGE_PROVIDER=real => настоящий packages/cv агента G (коммит 4fdb445 — сдан,
ImageIndex по контракту, qdrant_embedded, self-match 93.9%). Сверено
посимвольно с `cv/index.py` (задание оркестратора после урока ревью 02: "оба
берега хороши, пока швы не примерены"):
  - `Match(slug: str, score: float, gap: float | None, view: str)` — поля и
    типы идентичны app/cv/interface.py::Match; `view` использует латинское
    "real", НЕ "реальный" — контракт v0.4 сам ошибался в прозе ("реальный
    | synth-N"), v0.4.2 это поправил, MockImageIndex тоже приведён (было
    расхождение — см. reports/b-report.md).
  - Фабричной функции в пакете НЕТ (в отличие от packages/rag.get_retriever())
    — только класс `cv.index.ImageIndex`, конструктор без обязательных
    аргументов (`store`/`encoder`/`collection` — все опциональны, читают env
    сами через cv/config.py). Импорт — ИМЕННО `cv.index` (не просто `cv`,
    там пока только `__version__`), как явно указал оркестратор.
IMAGE_INDEX_MODE (qdrant_embedded|pgvector, contracts/image-scan.md) — это уже
ВНУТРЕННИЙ выбор бэкенда настоящего ImageIndex (cv/config.py), не имеет
отношения к этому файлу.

VERIFIER_PROVIDER (v0.4.4: переименовано из LABEL_VERIFIER_PROVIDER — короче,
симметрично IMAGE_PROVIDER) аналогично для LabelVerifier. v0.4.4 дала
реальную спецификацию, СДАНА агентом G коммитом `6a7e47a` (packages/cv/cv/
verify.py, PaddleOCR, p95=513мс на её замере — бюджет контракта <=700мс).
Тот же паттерн импорта, что и у ImageIndex: класс напрямую (`cv.verify.
LabelVerifier`), конструктор без обязательных аргументов (`lang`/
`score_thresh` — оба опциональны с разумными дефолтами). Сигнатура
`verify(image, candidates: list[VerifyCandidate])` сверена посимвольно с
app/cv/interface.py::LabelVerifier/VerifyCandidate при обновлении Protocol
под v0.4.4 (до её коммита) — совпало без расхождений.

Прогрев + офлайн (ревью 04, блокер 2): при IMAGE_PROVIDER=real модель
(SigLIP2, transformers) грузится ЛЕНИВО при первом embed/search — если это
происходит на первом боевом запросе, а не при старте процесса, пользователь
получает холодный старт вместо ответа (F2 намеряла ~340 с на чистом
окружении; в интеграционном тесте B тот же холодный кэш дал транзитный
ECONNRESET на HF Hub). Поэтому здесь же: (1) HF_HUB_OFFLINE/
TRANSFORMERS_OFFLINE выставляются ДО импорта cv.index, не только в тестах —
модель уже должна быть на диске (см. reports/b-report.md), сети до HF Hub не
нужно; (2) warm_up_image_index() — один search() заглушки СРАЗУ после
конструирования реального индекса, вызывается из app/main.py при старте
приложения, а не на первом запросе. Результат — app.state.image_index_warm,
наружу — GET /healthz.warm.

v0.4.7 (контракт §5, TODO-1 ревью 05): та же дисциплина теперь и для
LabelVerifier — warm_up_label_verifier() ниже, холостой verify() заглушки на
VERIFIER_PROVIDER=real (PaddleOCR тоже грузится лениво, packages/cv/cv/
verify.py::LabelVerifier._load()). Репетиция B3 (c03edd0) намерила +2 с
первому боевому near-dup запросу и списала это на ленивый PaddleOCR —
warm_up_image_index() грел только энкодер (embed()), не верификатор.

НАХОДКА этой волны (измерено напрямую, в обход HTTP — reports/
b4-gate-v047.md): диагноз "+2 с = PaddleOCR" был НЕПОЛНЫМ. С прогретым
верификатором первый БОЕВОЙ /scan/photo всё равно нёс ~1.8-1.9 с — не OCR.
Настоящий источник — embedded Qdrant-стор (packages/cv/cv/store.py):
embed() прогревает ТОЛЬКО энкодер SigLIP2, вообще не касаясь store; ПЕРВЫЙ
QdrantStore.search() на коллекции из 49650 точек стоит ~2 с САМ ПО СЕБЕ
(предупреждение самого клиента: "Local mode is not recommended for
collections with more than 20000 points"), все следующие — ~30 мс. Поэтому
warm_up_image_index() теперь зовёт search(), не embed() — заодно прогревает
и энкодер (search() вызывает его внутри себя), embed()-прогрев отдельно стал
избыточен. Результат обоих прогревов — app.state.image_index_warm/
label_verifier_warm; GET /healthz.warm — AND обоих.

agents/H2-rapidocr-multiscale.md: warm_up_label_verifier() теперь греет ВЫБРАННЫЙ
движок чтения текста запроса (`verifier.ocr_engine`, packages/cv/cv/verify.py) —
"paddle" (дефолт) не изменился (холостой verify()); "rapid" греет
`cv.ocr_rapid.RapidOcrReader` через `read_query_text()`, НЕ verify() (та на своём
внутреннем fallback-пути всегда грузит PaddleOCR — прогрев через неё при rapid
свёл бы на нет экономию RAM/времени старта, ради которой CV_OCR_ENGINE=rapid и
существует). GET /healthz.warm не меняется — AND обоих прогревов, как раньше.
"""
from __future__ import annotations

import io
import logging
import os
import struct
import time
import zlib

from ..config import Settings
from . import vision_llm
from .interface import ImageIndex, LabelVerifier, VerifyCandidate
from .mock import MockImageIndex, MockLabelVerifier

logger = logging.getLogger(__name__)


def get_image_index(settings: Settings) -> ImageIndex:
    provider = settings.image_provider
    if provider == "mock":
        return MockImageIndex()
    if provider == "real":
        # Ревью 04, блокер 2: ДО импорта cv.index (тот тянет transformers/
        # huggingface_hub) — иначе первый же холодный запрос на живом сервере
        # рискует сетевым обращением к HF Hub (ECONNRESET уже случался в
        # интеграционном тесте B на полностью валидном локальном кэше).
        # setdefault — не переопределяем, если оператор явно попросил иное.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from cv import index as _cv_index  # packages/cv/cv/index.py, зона агента G
        except ImportError as exc:
            raise RuntimeError(
                "IMAGE_PROVIDER=real, но пакет packages/cv не установлен в это "
                "окружение (uv pip install -e ../../packages/cv, или "
                "uv sync --extra integration в apps/api). Пока он не нужен — "
                "используйте IMAGE_PROVIDER=mock (дефолт)."
            ) from exc
        if hasattr(_cv_index, "get_image_index"):
            return _cv_index.get_image_index()
        if hasattr(_cv_index, "ImageIndex"):
            return _cv_index.ImageIndex()
        raise RuntimeError(
            "packages/cv установлен, но cv.index не предоставляет ни "
            "get_image_index(), ни класс ImageIndex() без аргументов — "
            "согласуйте способ инстанцирования с агентом G."
        )
    if provider == "winescan":
        # Второй движок распознавания (packages/winescan): детектор бутылки OWLv2 с обучаемым
        # выбором рамки, SigLIP 2 so400m в двух видах (упаковка и этикетка), мультиракурсная
        # галерея, проверка кандидатов по локальным признакам, рамка пользователя. Оркестрация
        # не меняется — движок отдаёт те же Match(slug, score, gap, view); какой провайдер
        # становится умолчанием, решает общий замер (docs/scan-engines.md).
        #
        # Офлайн-режим выставляется до импорта, как и для `real`: пакет тянет transformers,
        # и первый холодный запрос иначе рискует сходить в сеть за весами.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from winescan.integration import vinchik_index as _winescan
        except ImportError as exc:
            raise RuntimeError(
                "IMAGE_PROVIDER=winescan, но пакет packages/winescan не установлен в это "
                'окружение (uv pip install -e ../../packages/winescan"[ml]"). '
                "Используйте IMAGE_PROVIDER=mock (дефолт) или real."
            ) from exc
        return _winescan.get_image_index()
    raise ValueError(f"Неизвестный IMAGE_PROVIDER={provider!r}, ожидается mock|real|winescan")


def get_label_verifier(settings: Settings) -> LabelVerifier:
    provider = settings.verifier_provider
    if provider == "mock":
        return MockLabelVerifier()
    if provider == "real":
        try:
            from cv import verify as _cv_verify  # packages/cv/cv/verify.py, зона агента G, коммит 6a7e47a
        except ImportError as exc:
            raise RuntimeError(
                "VERIFIER_PROVIDER=real, но пакет packages/cv не установлен в это "
                "окружение (uv sync --extra integration в apps/api, тянет "
                "paddleocr/paddlepaddle — тяжёлые зависимости). Пока он не нужен — "
                "используйте VERIFIER_PROVIDER=mock (дефолт)."
            ) from exc
        # Нет фабричной функции (тот же паттерн, что у cv.index) — класс
        # напрямую, конструктор без обязательных аргументов. PaddleOCR
        # грузится ЛЕНИВО внутри (LabelVerifier._load(), первый verify()) —
        # конструктор здесь сам по себе дешёвый, не требует отдельного
        # прогрева на уровне фабрики (в отличие от warm_up_image_index()
        # для энкодера) — вызывающий код прогревает явным verify(), если
        # хочет исключить cold-start из первого боевого запроса.
        return _cv_verify.LabelVerifier()
    raise ValueError(f"Неизвестный VERIFIER_PROVIDER={provider!r}, ожидается mock|real")


def _tiny_placeholder_png() -> bytes:
    """2x2 белый PNG, валидный для любого декодера (PIL/opencv) — только
    stdlib (struct+zlib), без зависимости от Pillow в apps/api. Нужен
    исключительно как вход для warm_up_image_index(). Не 1x1: packages/cv/cv/
    imageio.py::decode_image() требует shape[0]>=2 и shape[1]>=2 (проверено
    эмпирически — 1x1 давал честный ValueError у decode_image самого, не у
    прогрева) — 2x2 минимальный размер, который реально проходит."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    width = height = 2
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 2x2, 8-bit, truecolor RGB
    row = b"\x00" + bytes([255, 255, 255]) * width  # filter=none + width белых пикселей
    idat = zlib.compress(row * height, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


_PLACEHOLDER_IMAGE = _tiny_placeholder_png()


def warm_up_image_index(image_index: ImageIndex, settings: Settings) -> bool:
    """Один search() заглушки СРАЗУ при старте процесса (не на первом боевом
    запросе) — ревью 04, блокер 2. На IMAGE_PROVIDER=mock прогрев не нужен —
    мок мгновенный, возвращаем True без вызова (нечего греть). Ошибка
    прогрева НЕ роняет старт приложения (лучше поднятый процесс с warm=False,
    чем не поднятый вовсе) — /healthz.warm сигнализирует состояние наружу.

    v0.4.7 (TODO-1 ревью 05, "первый запрос без +2 с"): ИЗМЕНЕНО с embed() на
    search() этой волной — ИЗМЕРЕНО напрямую (в обход HTTP, packages/cv/cv/
    index.py::ImageIndex.search()/store.py::QdrantStore.search()), что
    embed() прогревает ТОЛЬКО энкодер (SigLIP2), а search() дополнительно
    идёт в embedded Qdrant-стор — а у ТОГО отдельный, гораздо более дорогой
    одноразовый холодный старт при первом обращении к коллекции (49650
    точек, "Local mode is not recommended for collections with more than
    20000 points" — предупреждение самого клиента): FIRST store.search() ~2 c,
    ВСЕ последующие ~30 мс, embed() (без похода в store) — считанные мс что
    прогретый, что нет. Первоначальная гипотеза TODO-1 ("+2 c — ленивый
    PaddleOCR") оказалась НЕ основной причиной: с прогретым верификатором
    (warm_up_label_verifier ниже) первый БОЕВОЙ /scan/photo всё равно нёс
    ~1.8-1.9 с — ровно холодный Qdrant, не OCR (см. reports/b4-gate-v047.md
    §"Прогрев" за цифрами обоих экспериментов). embed() как ТАКОВОЙ прогрев
    энкодера теперь избыточен — search() делает то же самое внутри себя."""
    if settings.image_provider not in {"real", "winescan"}:
        return True
    try:
        image_index.search(_PLACEHOLDER_IMAGE, top_k=1)
        return True
    except Exception:
        return False


# v0.4.7 (контракт §5, TODO-1 ревью 05): фиктивный кандидат для прогрева
# верификатора — НЕ пустой список. `cv.verify.LabelVerifier.verify()`
# возвращает None РАНЬШЕ вызова read_text()/_load() именно на пустом списке
# кандидатов (`if not candidates: return None`, packages/cv/cv/verify.py) —
# прогрев с candidates=[] был бы пустышкой: PaddleOCR так и остался бы не
# загружен, а первый БОЕВОЙ near-dup запрос всё равно поймал бы холодный
# старт (репетиция B3 намерила +2 с первому запросу, c03edd0). slug/name —
# заведомо не совпадут ни с одним настоящим кандидатом (см. match_candidates()
# в cv/verify.py) — результат сопоставления не важен и осознанно
# отбрасывается, важен только побочный эффект: OCR-модель загружена.
_WARMUP_VERIFY_CANDIDATES: list[VerifyCandidate] = [
    {"slug": "__warmup__", "name": "", "vintage": None},
]


# Задача тимлида 22.09 (страховка лимита 10с приватной проверки, reports/
# devops-hack-v13.md): пути системных шрифтов с поддержкой кириллицы, СРЕДИ УЖЕ
# УСТАНОВЛЕННЫХ в ОС — никаких сетевых загрузок (правила безопасности агента:
# "Downloading or executing files from untrusted sources" запрещено). Первый
# существующий побеждает. `infra/ams3/bootstrap.sh` (стенд, Linux) шрифты НЕ
# ставит вовсе — сервер может не иметь НИ ОДНОГО кириллического TTF; на этот
# случай ниже (`_label_font`) есть честный фолбэк на `ImageFont.load_default()`
# (см. её докстринг про то, почему это не ломает сам смысл прогрева).
_CYRILLIC_FONT_CANDIDATES: tuple[str, ...] = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",  # macOS (дев-машина)
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Debian/Ubuntu, fonts-dejavu-core
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
)


def _label_font(size: int):
    """Шрифт для `_synthetic_label_image()` — первый кириллический кандидат,
    который реально есть на диске И грузится; иначе `PIL.ImageFont.
    load_default(size=...)` (Pillow >=10.1 — pyproject.toml пином >=10.4).
    Дефолтный шрифт PIL кириллицу не покрывает (рисует "тофу"-прямоугольники,
    проверено эмпирически) — но это НЕ ломает функциональную цель прогрева:
    цифры/латиница на этикетке ("2021", "0.75", "13%", см. `_synthetic_label_
    image()`) читаются движком ЛЮБЫМ шрифтом и уже достаточны, чтобы детектор
    текста нашёл боксы и третий проход RapidOCR по кропу этикетки
    (CV_OCR_LABEL_SIZE) реально выполнил инференс, а не только сконструировал
    движок (проверено эмпирически на реальном RapidOCR — см. отчёт)."""
    for path in _CYRILLIC_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                from PIL import ImageFont

                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001 — шрифт есть, но не грузится: пробуем следующий/фолбэк
                continue
    from PIL import ImageFont

    return ImageFont.load_default(size=size)


def _synthetic_label_image() -> bytes:
    """PIL-картинка этикетки с кириллическим текстом для прогрева текстовой ветки
    CV_FUSION (задача тимлида 22.09, п.2) — СГЕНЕРИРОВАНА, не фото кейса (в git
    можно, `case-data/` не трогается). Несколько строк разного размера по образцу
    реальной этикетки (винодельня/название/сорт/категория/год/объём),
    отцентрованных в нижней половине кадра — в той же зоне, что `CENTER_CROP`
    (0.15/0.05/0.85/0.98, `cv.verify.CENTER_CROP` он же `app/cv/vision_llm.py::
    CENTER_CROP`) вырезает у РЕАЛЬНОГО фото запроса, так что и RapidOCR/PaddleOCR
    (`LabelVerifier.read_query_text()`), и VLM (`vision_llm.read_label()`) видят
    текст в ожидаемом месте кадра, как в бою.

    Ленивый импорт PIL (не на верху модуля) — та же дисциплина, что у остальных
    ленивых импортов пакета (не тянуть лишнее в модули, которые попадают в
    процесс всегда): Pillow уже базовая зависимость apps/api (case_thumbs.py),
    так что импорт здесь дешёвый и гарантированно доступен."""
    from PIL import Image, ImageDraw

    width, height = 900, 1300
    img = Image.new("RGB", (width, height), (243, 240, 233))
    draw = ImageDraw.Draw(img)
    lines = (
        ("ВИНОДЕЛЬНЯ ТЕСТОВАЯ", 46),
        ("Шато Прогрев Резерв", 40),
        ("Каберне Совиньон", 32),
        ("СУХОЕ КРАСНОЕ ВИНО", 28),
        ("2021", 30),
        ("0.75 л  13% об.", 26),
    )
    y = int(height * 0.40)
    for text, size in lines:
        font = _label_font(size)
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (x1 - x0)) / 2, y), text, fill=(15, 15, 15), font=font)
        y += size + 18
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _warm_up_cv_fusion_text_branch(verifier: LabelVerifier, settings: Settings) -> bool:
    """Задача тимлида 22.09, п.2 (страховка лимита 10с приватной проверки): прогрев
    ВЫШЕ (плейсхолдер 2x2 белый) греет движки ФОРМАЛЬНО (конструктор ONNX/
    PaddleOCR вызывается на КАЖДОМ масштабе — см. `RapidOcrReader._engine_for()`,
    packages/cv/cv/ocr_rapid.py), но БЕЗ единого бокса текста третий проход
    RapidOCR по кропу этикетки (`CV_OCR_LABEL_SIZE`, дефолт 1280px) не выполняет
    РЕАЛЬНЫЙ инференс вовсе — `label_bbox()` требует хотя бы один найденный бокс,
    которого на пустой картинке нет (см. докстринг `_read_label_pass` в
    ocr_rapid.py: движок КОНСТРУИРУЕТСЯ всегда, но вызывается только если боксы
    есть). Здесь — ОДИН полный проход текстовой ветки на синтетической этикетке С
    НАСТОЯЩИМ ТЕКСТОМ (`_synthetic_label_image()`): `read_query_text()` (RapidOCR
    во ВСЕХ масштабах, включая третий проход по кропу этикетки — или PaddleOCR,
    смотря по `CV_OCR_ENGINE`, дисциплина та же, что у прогрева выше) + один
    запрос к VLM-шлюзу (GPU-сервер, `VISION_LLM_URL` — наш шлюз, сетевой вызов
    разрешён брифом), если адрес задан. Эмпирически проверено на реальном
    RapidOCR (packages/cv, апробация этой волны): синтетическая картинка выше
    даёт boxes на масштабе 960px и валидный `label_bbox()` — третий проход
    (1280px) реально выполняет `_run_engine()`, не только конструирует движок.

    Только при `settings.cv_fusion` (нечего греть, если слияние выключено —
    `_fusion_text_and_vectors()` в этом режиме не вызывается вовсе). Сбой
    ЛОКАЛЬНОГО OCR — тот же честный сигнал, что и у прогрева выше (влияет на
    возврат этой функции, а через него — на итоговый `warm_up_label_verifier()`).
    Сбой VLM (внешняя сеть, шлюз может быть занят/недоступен на старте —
    ПРЯМОЕ указание брифа) — НЕ должен ни ронять старт процесса, ни переводить
    прогрев в `warm=false`: только WARNING в лог (`CV_FUSION` и без ответившей
    VLM продолжает работать честным фолбэком на OCR, см. `_fusion_text_and_
    vectors`) — ключ шлюза не логируется (тот же принцип, что `vision_llm.py`)."""
    if not settings.cv_fusion:
        return True
    start = time.monotonic()
    image_bytes = _synthetic_label_image()
    try:
        verifier.read_query_text(image_bytes)
        ok = True
    except Exception as exc:  # noqa: BLE001 — прогрев не должен ронять старт процесса
        logger.warning("cv_fusion warm-up: OCR текстовой ветки не прогрелся (%s)", type(exc).__name__)
        ok = False
    if settings.vision_llm_url:
        try:
            vision_llm.read_label(
                image_bytes,
                url=settings.vision_llm_url, key=settings.vision_llm_key, model=settings.vision_llm_model,
                timeout_s=settings.vision_llm_timeout_s, image_size=settings.vision_llm_image_size,
            )
        except Exception as exc:  # noqa: BLE001 — внешняя сеть: не блокирует старт, не портит warm
            logger.warning(
                "cv_fusion warm-up: запрос к VLM-шлюзу не удался (%s) — не блокирует старт, warm не меняет",
                type(exc).__name__,
            )
    logger.info(
        "cv_fusion warm-up: текстовая ветка прогрета за %.2fс (ocr_ok=%s)",
        time.monotonic() - start, ok,
    )
    return ok


def warm_up_label_verifier(verifier: LabelVerifier, settings: Settings) -> bool:
    """Холостой прогрев ВЫБРАННОГО движка чтения текста запроса СРАЗУ при старте —
    симметрично warm_up_image_index(). Прогревается ТОЛЬКО при
    VERIFIER_PROVIDER=real (мок мгновенный, нечего греть — то же правило, что и у
    image_index). Ошибка прогрева не роняет старт (см. warm_up_image_index) —
    /healthz.warm сигнализирует состояние наружу (AND обоих прогревов, см.
    app/routers/health.py).

    agents/H2-rapidocr-multiscale.md: `verifier.ocr_engine == "rapid"` — греет
    `RapidOcrReader` через `read_query_text()` (`cv.verify.LabelVerifier.
    read_query_text_rapid()`), а НЕ `verify()` — тот на своём внутреннем
    fallback-пути (без `ocr_text`) ВСЕГДА грузит PaddleOCR, независимо от
    `CV_OCR_ENGINE` (packages/cv/cv/verify.py, докстринг "Движок текста запроса"),
    так что прогрев через `verify()` при rapid молча тащил бы PaddleOCR в память —
    ровно та трата RAM/времени старта на дешёвом CPU, которую rapid должен
    экономить (brief п.2). `getattr(..., "paddle")` — мягкий дефолт: двойники
    тестов этого файла (без реального `LabelVerifier`) не несут `ocr_engine`
    вовсе и обязаны сохранить СТАРОЕ поведение (`verify()`), не молча
    переключиться на новую ветку.

    Задача тимлида 22.09, п.2: ДОБАВЛЯЕТ `_warm_up_cv_fusion_text_branch()` —
    прогрев на синтетической этикетке С ТЕКСТОМ поверх плейсхолдера выше (не
    вместо него — плейсхолдер остаётся байт-в-байт для всех существующих
    сценариев/тестов этого файла, работает и когда `CV_FUSION=0`). Срабатывает
    ТОЛЬКО при `settings.cv_fusion` — см. её докстринг."""
    if settings.verifier_provider != "real":
        return True
    try:
        if getattr(verifier, "ocr_engine", "paddle") == "rapid":
            verifier.read_query_text(_PLACEHOLDER_IMAGE)
        else:
            verifier.verify(_PLACEHOLDER_IMAGE, _WARMUP_VERIFY_CANDIDATES)
        ok = True
    except Exception:
        ok = False
    if settings.cv_fusion:
        ok = _warm_up_cv_fusion_text_branch(verifier, settings) and ok
    return ok
