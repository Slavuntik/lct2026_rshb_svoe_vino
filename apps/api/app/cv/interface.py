"""Зеркало contracts/image-scan.md v0.4 (ЗАМОРОЖЁН, правки — через
оркестратора). Пакет packages/cv — зона агента G; на момент написания этого
кода пуст (датасет кейса ещё не приехал, packages/cv/ не создан вовсе). По
тому же паттерну, что и app/rag/interface.py для агента A: ImageIndex
продублирован здесь дословно как структурный (duck-typed) Protocol,
MockImageIndex в mock.py его реализует, app/cv/factory.py переключается на
настоящий packages/cv через CV_PROVIDER=real, если/когда пакет появится.

LabelVerifier (OCR-верификатор) — contracts/image-scan.md описывает его роль
в пайплайне ("OCR-верификатор (год, категория, объём с этикетки)"), но не
даёт готовой сигнатуры класса — она из задачи оркестратора этой волны:
`verify(image_bytes, candidates: list[str]) -> str | None`. Реализация вне
зоны B ("реализация придёт позже") — здесь только Protocol и мок по
фикстурам, аналогично ImageIndex. НЕ в packages/llm (прямое указание
оркестратора) — лежит здесь же, рядом с остальным зеркалом CV-контракта.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Match:
    slug: str
    score: float  # сравним только внутри одного ответа (как rag.Candidate.score)
    gap: float | None  # отрыв от следующего НЕ-той-же-группы кандидата
    view: str  # какой ракурс эталона сматчился (реальный | synth-N)


class ImageIndex(Protocol):
    def embed(self, image: bytes) -> list[float]: ...

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        """Запрос проходит нормализацию ДО эмбеддинга: детект этикетки ->
        кроп -> выпрямление цилиндра -> фотометрия. Без нормализации —
        только явным флагом. Битый/пустой файл -> ValueError, не
        500-полуфабрикат (см. agents/G-cv.md, тесты)."""

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        """slug -> список файлов (эталон + синтетические ракурсы). Версия в манифест."""

    def add(self, slug: str, images: list[bytes]) -> None:
        """+50 позиций/день без ребилда всего индекса."""


class LabelVerifier(Protocol):
    def verify(self, image_bytes: bytes, candidates: list[str]) -> str | None:
        """Среди near-dup кандидатов (одна этикетка, разные год/категория/
        объём) выбирает точный slug по тексту на фото (OCR), либо None,
        если не смог уверенно различить — вызывающий код тогда честно
        откатывается на top-1 ANN без ocr_verified=True."""


def get_image_index() -> ImageIndex:  # pragma: no cover - см. app/cv/factory.py
    """Симметрично rag.get_retriever()/llm.get_llm(): ожидаемая точка входа
    настоящего packages/cv (её там пока нет — пакет пуст). apps/api вместо
    неё использует app.cv.factory.get_image_index(settings)."""
    raise NotImplementedError("реализация — в пакете packages/cv, не здесь")
