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
нужно; (2) warm_up_image_index() — один embed() заглушки СРАЗУ после
конструирования реального индекса, вызывается из app/main.py при старте
приложения, а не на первом запросе. Результат — app.state.image_index_warm,
наружу — GET /healthz.warm.
"""
from __future__ import annotations

import os
import struct
import zlib

from ..config import Settings
from .interface import ImageIndex, LabelVerifier
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
    raise ValueError(f"Неизвестный IMAGE_PROVIDER={provider!r}, ожидается mock|real")


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
    """Один embed() заглушки СРАЗУ при старте процесса (не на первом боевом
    запросе) — ревью 04, блокер 2. На IMAGE_PROVIDER=mock прогрев не нужен —
    мок мгновенный, возвращаем True без вызова (нечего греть). Ошибка
    прогрева НЕ роняет старт приложения (лучше поднятый процесс с warm=False,
    чем не поднятый вовсе) — /healthz.warm сигнализирует состояние наружу."""
    if settings.image_provider != "real":
        return True
    try:
        image_index.embed(_PLACEHOLDER_IMAGE)
        return True
    except Exception:
        return False
