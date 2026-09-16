"""MockImageIndex/MockLabelVerifier — реализация ImageIndex/LabelVerifier
(contracts/image-scan.md) без реальных фото и моделей — до готовности
packages/cv (agents/G-cv.md: "не жди его", тот же паттерн, что и
MockRetriever для packages/rag).

Условная кодировка "фото" в bytes (реальных изображений ещё нет — датасет
кейса не приехал, packages/cv пуст):
  b""                                    -> ValueError (битый/пустой файл)
  b"MOCKPHOTO:<slug>"                     -> уверенный матч на <slug> (из фикстур)
  b"MOCKPHOTO:weak:<slug>"                -> тот же <slug>, низкий score
                                             (для сценария not_in_catalog)
  b"MOCKPHOTO:near-dup"                   -> топ-2 из NEAR_DUP_GROUP выше границы
                                             группы + одна "чужая" позиция РОВНО
                                             на границе (score = top.score - gap,
                                             contracts v0.4.2: gap — отрыв до
                                             следующего НЕ-той-же-группы кандидата,
                                             не до соседа по рангу) -> включает
                                             OCR-ветку пайплайна на двух первых
  b"MOCKPHOTO:near-dup:<slug>"            -> то же + MockLabelVerifier "прочтёт"
                                             именно <slug>, если он в кандидатах
  b"MOCKPHOTO:near-dup:NONE"              -> то же, но верификатор честно не
                                             смог различить (None)
  b"MOCKPHOTO:unknown"                    -> пустой список (ничего похожего)
  любые другие байты (настоящее фото, если кто-то уже шлёт) -> не падаем,
                                             детерминированный выбор по хэшу,
                                             умеренный score — мок не умеет
                                             "видеть" реальные фото осмысленно,
                                             но и не роняет пайплайн
"""
from __future__ import annotations

import hashlib

from .fixtures import ALL_SLUGS, CONFIDENT_SLUGS, INDEX_VERSION, NEAR_DUP_GROUP, NEAR_DUP_OCR_ANSWER
from .interface import Match

_CONFIDENT_SCORE = 0.93
_CONFIDENT_GAP = 0.2
_WEAK_SCORE = 0.32
_WEAK_GAP = 0.15
_NEAR_DUP_SCORE = 0.88
_NEAR_DUP_PARTNER_SCORE = 0.875  # второй член ТОЙ ЖЕ группы — строго между top1 и границей группы
_NEAR_DUP_GAP = 0.01  # v0.4.2: отрыв ИМЕННО до следующего НЕ-той-же-группы кандидата
                       # (contracts/image-scan.md), а не до соседа по рангу — оба члена
                       # near-dup группы обязаны сидеть строго выше границы (top.score - gap),
                       # её определяет третья, реально другая позиция ниже (см. search()).
_NEAR_DUP_OTHER_SLUG = CONFIDENT_SLUGS[0]  # "чужая" позиция ровно на границе группы
_FALLBACK_SCORE = 0.6
_FALLBACK_GAP = 0.15

_NEAR_DUP_PREFIX = "MOCKPHOTO:near-dup"
_WEAK_PREFIX = "MOCKPHOTO:weak:"
_VERIFY_PREFIX = "MOCKPHOTO:near-dup:"


class MockImageIndex:
    index_version = INDEX_VERSION

    def __init__(self) -> None:
        self._added: dict[str, list[bytes]] = {}

    def embed(self, image: bytes) -> list[float]:
        if not image:
            raise ValueError("пустое изображение — нечего эмбеддить")
        digest = hashlib.sha256(image).digest()
        return [b / 255.0 for b in digest[:16]]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        if not image:
            raise ValueError("пустое изображение — search на битом файле недопустим")

        text = image.decode("utf-8", errors="ignore")

        if text.startswith(_WEAK_PREFIX):
            slug = text[len(_WEAK_PREFIX):].strip()
            if slug in ALL_SLUGS:
                return [Match(slug=slug, score=_WEAK_SCORE, gap=_WEAK_GAP, view="synth-1")]
            return []

        if text.startswith(_NEAR_DUP_PREFIX):
            return [
                Match(slug=NEAR_DUP_GROUP[0], score=_NEAR_DUP_SCORE, gap=_NEAR_DUP_GAP, view="real"),
                Match(slug=NEAR_DUP_GROUP[1], score=_NEAR_DUP_PARTNER_SCORE,
                      gap=_NEAR_DUP_GAP, view="synth-2"),
                # Граница группы: score РОВНО top.score - gap -> run_photo_scan()
                # обязан её ИСКЛЮЧИТЬ из кандидатов на OCR (не строго больше границы).
                Match(slug=_NEAR_DUP_OTHER_SLUG, score=round(_NEAR_DUP_SCORE - _NEAR_DUP_GAP, 4),
                      gap=None, view="real"),
            ][:top_k]

        if text.startswith("MOCKPHOTO:unknown"):
            return []

        if text.startswith("MOCKPHOTO:"):
            slug = text[len("MOCKPHOTO:"):].strip()
            if slug in ALL_SLUGS:
                return [Match(slug=slug, score=_CONFIDENT_SCORE, gap=_CONFIDENT_GAP, view="real")]
            return []

        # Не наша конвенция (настоящие байты фото) — детерминированная
        # деградация вместо падения.
        idx = int(hashlib.sha256(image).hexdigest(), 16) % len(CONFIDENT_SLUGS)
        return [Match(slug=CONFIDENT_SLUGS[idx], score=_FALLBACK_SCORE, gap=_FALLBACK_GAP, view="real")]

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        # Фикстуры статические — мок ничего не переиндексирует. Метод есть
        # только для соответствия Protocol.
        return None

    def add(self, slug: str, images: list[bytes]) -> None:
        self._added.setdefault(slug, []).extend(images)


class MockLabelVerifier:
    """См. докстринг модуля — управляется той же MOCKPHOTO-конвенцией."""

    def verify(self, image_bytes: bytes, candidates: list[str]) -> str | None:
        text = image_bytes.decode("utf-8", errors="ignore")
        if text.startswith(_VERIFY_PREFIX):
            requested = text[len(_VERIFY_PREFIX):].strip()
            if requested == "NONE":
                return None
            return requested if requested in candidates else None
        if NEAR_DUP_OCR_ANSWER in candidates:
            return NEAR_DUP_OCR_ANSWER
        return None
