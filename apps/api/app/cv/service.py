"""Пайплайн /scan/photo (contracts/image-scan.md): ANN top-K -> near-dup
routing по gap -> OCR-верификатор -> confident/not_in_catalog решение.

Порог "уверенности" (CV_CONFIDENT_SCORE_THRESHOLD) и порог "маленького
gap" (CV_NEAR_DUP_GAP_THRESHOLD) — оба ПЛЕЙСХОЛДЕРЫ до приезда датасета
кейса и реальной калибровки на голд-сете (см. reports/b-report.md, раздел
«Кейс: /scan/photo» — пересчитать, когда появится eval на настоящих данных,
как это уже сделал агент A для текстового ретривера). Значения и their env
— app/config.py.

run_photo_scan() НЕ решает confident/not_in_catalog единолично — отдаёт
PhotoScanResult с обоими "срезами" (best_guess_slug — всегда, для flat;
slug — только если уверенно, для rich) и timing_ms, измеренным по-настоящему
вокруг всего пайплайна (embed/search внутри ImageIndex, плюс OCR, если он
вызывался). confidence.f1_* и eval_missing — забота вызывающего роутера
(см. app/cv/eval_report.py) — это system-level метрика, не per-запросная.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable

from ..config import Settings
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


def _dedupe_preserve_order(slugs: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for s in slugs:
        if s not in seen:
            seen.append(s)
    return seen


def _hydrate_card(retriever: Retriever, slug: str) -> dict | None:
    candidate = retriever.get_by_id(slug)
    if candidate is None or candidate.kind != "wine":
        return None
    return {
        "wine_id": slug,
        "source": candidate.meta["source"],
        "derived": candidate.meta["derived"],
        "source_url": candidate.url,
    }


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
        )

    top = matches[0]
    chosen_slug = top.slug
    ocr_verified = False

    # Near-dup routing: маленький gap = топ рядом с чужой-другой позицией ->
    # звать OCR по всем РАЗЛИЧНЫМ слагам среди топ-кандидатов (contracts/
    # image-scan.md: "если топ-позиции из одной near-dup-группы").
    if top.gap is not None and top.gap < settings.cv_near_dup_gap_threshold:
        candidate_slugs = _dedupe_preserve_order(m.slug for m in matches[:top_k])
        if len(candidate_slugs) > 1:
            verified = verifier.verify(image_bytes, candidate_slugs)
            if verified is not None and verified in candidate_slugs:
                chosen_slug = verified
                ocr_verified = True

    best_guess_slug = chosen_slug
    confident = top.score >= settings.cv_confident_score_threshold

    card = _hydrate_card(retriever, chosen_slug) if confident else None
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
    )
