"""Пайплайн /scan/photo (contracts/image-scan.md): ANN top-K -> near-dup
routing по gap -> OCR-верификатор -> confident/not_in_catalog решение.

Порог "уверенности" (CV_CONFIDENT_SCORE_THRESHOLD) и порог "маленького
gap" (CV_NEAR_DUP_GAP_THRESHOLD) — оба ПЛЕЙСХОЛДЕРЫ до приезда датасета
кейса и реальной калибровки на голд-сете (см. reports/b-report.md, раздел
«Кейс: /scan/photo» — пересчитать, когда появится eval на настоящих данных,
как это уже сделал агент A для текстового ретривера). CV_NEAR_DUP_GAP_THRESHOLD
уже пересчитан ОДИН раз по реальному примеру (интеграционный тест на
настоящем ImageIndex поймал: исходный 0.05 был угадан по шкале мок-скоров и
не сработал бы вовсе на реальных данных, где near-dup пара дала gap=0.245)
— см. app/config.py. Значения и их env — там же.

run_photo_scan() НЕ решает confident/not_in_catalog единолично — отдаёт
PhotoScanResult с обоими "срезами" (best_guess_slug — всегда, для flat;
slug — только если уверенно, для rich) и timing_ms, измеренным по-настоящему
вокруг всего пайплайна (embed/search внутри ImageIndex, плюс OCR, если он
вызывался). confidence.f1_* и eval_missing — забота вызывающего роутера
(см. app/cv/eval_report.py) — это system-level метрика, не per-запросная.

v0.4.3 (пробел нашёл F): PhotoScanResult.matches — top-5 схлопнутых позиций
{slug, score} по убыванию, ВСЕГДА (независимо от near-dup/confident решения) —
baseline F показал, что без top-5 в живом API F1-top5 eval-раннера вырождается
в F1-top1 (ТЗ требует обе метрики отдельно). UI это поле не рендерит (одна
карточка — закон), flat-режим не меняется. `matches` уже схлопнуты в позиции
самим ImageIndex.search() (contracts/image-scan.md; см. packages/cv/cv/
index.py::search() — best_by_slug оставляет один Match на slug), здесь только
берём готовый список как есть.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from ..config import Settings
from ..rag.cards import build_wine_card
from ..rag.interface import Retriever
from .interface import ImageIndex, LabelVerifier, Match


@dataclass
class PhotoScanResult:
    best_guess_slug: str | None  # flat-режим: ВСЕГДА лучший доступный, даже низкая уверенность
    slug: str | None  # rich-режим: только если уверенно (иначе None -> not_in_catalog)
    card: dict | None
    top1_score: float | None
    gap: float | None
    ocr_verified: bool
    not_in_catalog: bool
    timing_ms: int
    similar: list[dict] = field(default_factory=list)
    analogs: list[dict] = field(default_factory=list)
    matches: list[dict] = field(default_factory=list)  # v0.4.3: top-5 {slug, score} для eval F1-top5


def _dedupe_preserve_order(slugs: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.append(s)
    return seen


def _match_items(matches: list[Match], limit: int = 5) -> list[dict]:
    """v0.4.3: {slug, score} по убыванию, независимо от `top_k`, с которым был
    вызван поиск — контракт фиксирует именно top-5 для eval, а не "top_k"."""
    return [{"slug": m.slug, "score": m.score} for m in matches[:limit]]


def _wine_item(retriever: Retriever, slug: str) -> dict | None:
    """Лёгкая карточка для similar/analogs — та же форма, что AnalogsWineItem
    в схемах API (переиспользуем схему поверх разных источников)."""
    candidate = retriever.get_by_id(slug)
    if candidate is None or candidate.kind != "wine":
        return None
    source = candidate.meta["source"]
    return {
        "wine_id": slug,
        "name": source.get("name", slug),
        "winery_name": source.get("winery_name", ""),
        "region_name": source.get("region_name", ""),
    }


def _suggest_similar_and_analogs(
    retriever: Retriever, matches: list[Match], best_slug: str
) -> tuple[list[dict], list[dict]]:
    similar: list[dict] = []
    for slug in _dedupe_preserve_order(m.slug for m in matches):
        item = _wine_item(retriever, slug)
        if item:
            similar.append(item)

    analogs: list[dict] = []
    candidate = retriever.get_by_id(best_slug)
    if candidate is not None and candidate.kind == "wine":
        styles = candidate.meta["derived"].get("reference_style_matches") or []
        if styles:
            for c in retriever.analog_for_style(styles[0], top_k=6):
                if c.id == best_slug:
                    continue
                source = c.meta["source"]
                analogs.append({
                    "wine_id": c.id, "name": source.get("name", c.id),
                    "winery_name": source.get("winery_name", ""),
                    "region_name": source.get("region_name", ""),
                })
    return similar, analogs


def run_photo_scan(
    *,
    image_bytes: bytes,
    image_index: ImageIndex,
    verifier: LabelVerifier,
    retriever: Retriever,
    settings: Settings,
    top_k: int = 5,
) -> PhotoScanResult:
    t0 = time.monotonic()

    matches = image_index.search(image_bytes, top_k=top_k)  # ValueError на битом файле — не ловим, пусть роутер решает код ответа

    if not matches:
        return PhotoScanResult(
            best_guess_slug=None, slug=None, card=None, top1_score=None, gap=None,
            ocr_verified=False, not_in_catalog=True,
            timing_ms=int((time.monotonic() - t0) * 1000),
            matches=[],
        )

    top = matches[0]
    chosen_slug = top.slug
    ocr_verified = False

    # Near-dup routing: маленький gap = топ рядом с чужой-другой позицией ->
    # звать OCR по всем РАЗЛИЧНЫМ слагам среди топ-кандидатов (contracts/
    # image-scan.md: "если топ-позиции из одной near-dup-группы").
    #
    # v0.4.2, найдено интеграционным тестом на реальном ImageIndex
    # (tests/test_integration_real_cv.py): кандидаты для OCR — НЕ голый срез
    # matches[:top_k], а именно позиции ТОЙ ЖЕ near-dup-группы, что и top1.
    # Контракт определяет gap как "отрыв от следующего НЕ-той-же-группы
    # кандидата" — а значит по построению у cv/index.py ВСЕ матчи со
    # score строго выше границы (top.score - top.gap) обязаны быть из одной
    # группы с top1 (иначе граница была бы посчитана раньше, на них). На
    # крошечном тестовом индексе (5 фото) matches[:top_k] отдавал ВСЕ 5 позиций
    # целиком, из них реально в группе с top1 — только 2; без этого фильтра
    # OCR-верификатор звался бы с заведомо чужими, далёкими по score слагами.
    if top.gap is not None and top.gap < settings.cv_near_dup_gap_threshold:
        group_floor = top.score - top.gap
        candidate_slugs = _dedupe_preserve_order(
            m.slug for m in matches[:top_k] if m.score > group_floor
        )
        if len(candidate_slugs) > 1:
            verified = verifier.verify(image_bytes, candidate_slugs)
            if verified is not None and verified in candidate_slugs:
                chosen_slug = verified
                ocr_verified = True

    best_guess_slug = chosen_slug
    confident = top.score >= settings.cv_confident_score_threshold

    # v0.4.1: card — ровно тело GET /wines/{id} (включая similar), общий
    # построитель с routers/wines.py (app/rag/cards.py) — не две формы.
    card = build_wine_card(retriever, chosen_slug) if confident else None
    similar: list[dict] = []
    analogs: list[dict] = []
    if not confident:
        similar, analogs = _suggest_similar_and_analogs(retriever, matches, top.slug)

    return PhotoScanResult(
        best_guess_slug=best_guess_slug,
        slug=chosen_slug if confident else None,
        card=card,
        top1_score=top.score,
        gap=top.gap,
        ocr_verified=ocr_verified,
        not_in_catalog=not confident,
        timing_ms=int((time.monotonic() - t0) * 1000),
        similar=similar,
        analogs=analogs,
        matches=_match_items(matches),
    )
