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
"""
from __future__ import annotations

import os
import struct
import zlib

from ..config import Settings
from .interface import ImageIndex, LabelVerifier, VerifyCandidate
from .mock import MockImageIndex, MockLabelVerifier


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
    if settings.image_provider != "real":
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


def warm_up_label_verifier(verifier: LabelVerifier, settings: Settings) -> bool:
    """Холостой verify() заглушки СРАЗУ при старте — симметрично
    warm_up_image_index(), но для PaddleOCR (packages/cv/cv/verify.py::
    LabelVerifier._load(), ленивая загрузка на первый verify()). Прогревается
    ТОЛЬКО при VERIFIER_PROVIDER=real (мок мгновенный, нечего греть — то же
    правило, что и у image_index). Ошибка прогрева не роняет старт (см.
    warm_up_image_index) — /healthz.warm сигнализирует состояние наружу (AND
    обоих прогревов, см. app/routers/health.py)."""
    if settings.verifier_provider != "real":
        return True
    try:
        verifier.verify(_PLACEHOLDER_IMAGE, _WARMUP_VERIFY_CANDIDATES)
        return True
    except Exception:
        return False
