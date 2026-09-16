"""Зеркало contracts/image-scan.md v0.4.2 (ЗАМОРОЖЁН, правки — через
оркестратора). Пакет packages/cv — зона агента G; сдан коммитом 4fdb445
(ImageIndex по контракту, qdrant_embedded, self-match 93.9%, search p95
30.8 мс). По тому же паттерну, что и app/rag/interface.py для агента A:
ImageIndex продублирован здесь дословно как структурный (duck-typed)
Protocol, MockImageIndex в mock.py его реализует, app/cv/factory.py
переключается на настоящий packages/cv через IMAGE_PROVIDER=real.

Сверено посимвольно с packages/cv/cv/index.py (задание оркестратора после
урока ревью 02 — "оба берега хороши, пока швы не примерены"): поля/типы
Match идентичны; единственное расхождение было в значении `view` для
эталонного ракурса — контракт v0.4 писал "реальный" в прозе, настоящий код
(и поправленный v0.4.2) используют латинское "real". MockImageIndex
приведён к этому же значению.

LabelVerifier (OCR-верификатор) — contracts/image-scan.md описывает его роль
в пайплайне ("OCR-верификатор (год, категория, объём с этикетки)"), но не
даёт готовой сигнатуры класса — она из задачи оркестратора, поставившей
CV-интеграцию: `verify(image_bytes, candidates: list[str]) -> str | None`.
Реализация вне зоны B и вне зоны G ("реализация придёт позже") — здесь
только Protocol и мок по фикстурам, аналогично ImageIndex. НЕ в
packages/llm (прямое указание оркестратора) — лежит здесь же, рядом с
остальным зеркалом CV-контракта.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Match:
    slug: str
    score: float  # сравним только внутри одного ответа (как rag.Candidate.score)
    gap: float | None  # отрыв от следующего НЕ-той-же-группы кандидата
    view: str  # какой ракурс эталона сматчился ("real" | "synth-N")


class ImageIndex(Protocol):
    def embed(self, image: bytes) -> list[float]: ...

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        """Запрос проходит нормализацию ДО эмбеддинга: детект этикетки ->
        кроп -> выпрямление цилиндра -> фотометрия. Без нормализации —
        только явным флагом. Битый/пустой файл -> ValueError, не
        500-полуфабрикат (см. agents/G-cv.md, тесты)."""

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        """slug -> список файлов (эталон + синтетические ракурсы). Версия в
        манифест. v0.4.2: конвенция порядка в списке — первый файл эталон
        (view="real"), остальные synth-1..N по позиции; менять нормализацию
        = пересобирать индекс заново (единый домен нормализации, см.
        уточнения v0.4.2 в contracts/image-scan.md)."""

    def add(self, slug: str, images: list[bytes]) -> None:
        """+50 позиций/день без ребилда всего индекса. Та же конвенция
        порядка, что у build() (v0.4.2)."""


class LabelVerifier(Protocol):
    def verify(self, image_bytes: bytes, candidates: list[str]) -> str | None:
        """Среди near-dup кандидатов (одна этикетка, разные год/категория/
        объём) выбирает точный slug по тексту на фото (OCR), либо None,
        если не смог уверенно различить — вызывающий код тогда честно
        откатывается на top-1 ANN без ocr_verified=True."""


def get_image_index() -> ImageIndex:  # pragma: no cover - см. app/cv/factory.py
    """Симметрично rag.get_retriever()/llm.get_llm() — но, в отличие от
    packages/rag, настоящий packages/cv такую фабричную функцию НЕ
    экспортирует (проверено: packages/cv/cv/__init__.py содержит только
    __version__). app/cv/factory.py::get_image_index(settings) при
    IMAGE_PROVIDER=real поэтому импортирует класс напрямую —
    `from cv.index import ImageIndex` — и пробует эту функцию только как
    опциональный, forward-совместимый вариант (hasattr-проверка)."""
    raise NotImplementedError("реализация — в пакете packages/cv, не здесь")
