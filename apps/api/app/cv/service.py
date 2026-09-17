"""Пайплайн /scan/photo (contracts/image-scan.md): ANN top-K -> near-dup
routing по gap -> OCR-верификатор -> confident/not_in_catalog решение.

Пороги — все ПЛЕЙСХОЛДЕРЫ до реальной калибровки на голд-сете кейса, но
разной степени "угаданности" (см. app/config.py, там же env-имена):
- CV_NEAR_DUP_GAP_THRESHOLD — пересчитан ОДИН раз по реальному near-dup
  примеру (aligote-barrel-2024/2025, gap=0.245 на настоящем ImageIndex,
  tests/test_integration_real_cv.py) — было 0.05, угадано по мок-шкале.
- CV_ABS_FLOOR — v0.4.5, реально откалиброван F2 на impostor-холдауте (45
  "чужих" вин против калибровочного индекса): чистый score-порог НЕ
  разделяет позитив/импостеров без неприемлемой цены, минимальный порог с
  FPR<=5% — 0.9 (ценой FNR=66.7%), см. qa/scan-eval-runs/
  not-in-catalog-calibration/report.md.
- CV_MARGIN_FLOOR — v0.4.5, добавлен КАК рычаг ПО ВЫВОДУ калибровки F2
  ("одного порога на сыром score недостаточно"), но само число НЕ
  откалибровано (заимствовано у CV_NEAR_DUP_GAP_THRESHOLD как общий якорь —
  оба читают Match.gap) — пересчитать отдельно, когда приедет датасет.

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
from .interface import ImageIndex, LabelVerifier, Match, VerifyCandidate


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


def _verify_candidates(retriever: Retriever, slugs: list[str]) -> list[VerifyCandidate]:
    """v0.4.4: LabelVerifier.verify() принимает метаданные из каталога, не
    голые slug'и — "B передаёт метаданные кандидатов из каталога (get_by_id)".
    Каталог может не знать конкретный slug (CV нашёл позицию, которой ещё/уже
    нет в каталожном слое — тот же реалистичный сценарий рассинхрона, что и у
    `card=None`) — тогда деградируем честно: name=slug, vintage=None, а не
    падаем и не роняем near-dup routing из-за пробела в каталоге."""
    result: list[VerifyCandidate] = []
    for slug in slugs:
        candidate = retriever.get_by_id(slug)
        if candidate is not None and candidate.kind == "wine":
            source = candidate.meta["source"]
            result.append(VerifyCandidate(
                slug=slug, name=source.get("name", slug), vintage=source.get("vintage"),
            ))
        else:
            result.append(VerifyCandidate(slug=slug, name=slug, vintage=None))
    return result


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
    #
    # v0.4.7 (контракт §3, TODO-0/2 ревью 05): gap=None БОЛЬШЕ НЕ значит "нет
    # near-dup соседей вовсе" — теперь это "в top-K нет кандидата ВНЕ семьи
    # top-1" (см. `confident` ниже). Из этого следует: если gap=None, то ВСЕ
    # различные слаги, которые вообще вернул search() (matches[:top_k]), по
    # определению — члены ОДНОЙ семьи top-1 (иначе first-outside-family
    # конкурент был бы найден раньше, и gap не был бы null). Старая версия
    # этого блока (`if top.gap is not None and top.gap < threshold`) в этом
    # случае near-dup routing целиком ПРОПУСКАЛА — на плотном каталоге gap
    # почти всегда null (F3 baseline, reports/f3-synthetic-baseline.md), а
    # значит OCR-верификатор внутри семьи фактически никогда не вызывался,
    # даже когда top-K реально держал 2+ членов одной семьи (диагноз review
    # 05 TODO-2, Мускатель Массандра). Ветка ниже чинит именно это — верификатор
    # обязан вызываться "независимо от исхода маржинальной проверки" (§3):
    # маржа (см. `confident`) на null-gap уже считается пройденной, но это не
    # повод пропускать различение ГОД/КАТЕГОРИЯ внутри семьи.
    if top.gap is None:
        candidate_slugs = _dedupe_preserve_order(m.slug for m in matches[:top_k])
    elif top.gap < settings.cv_near_dup_gap_threshold:
        group_floor = top.score - top.gap
        candidate_slugs = _dedupe_preserve_order(
            m.slug for m in matches[:top_k] if m.score > group_floor
        )
    else:
        candidate_slugs = [top.slug]  # конкурент вне семьи далёк — не near-dup случай

    if len(candidate_slugs) > 1:
        # v0.4.4: verify() хочет метаданные каталога (name/vintage), не
        # голые slug'и — см. _verify_candidates().
        verified = verifier.verify(image_bytes, _verify_candidates(retriever, candidate_slugs))
        if verified is not None and verified in candidate_slugs:
            chosen_slug = verified
            ocr_verified = True

    best_guess_slug = chosen_slug
    # v0.4.5 (ревью 04, калибровка F2 на impostor-холдауте): not_in_catalog
    # переведён с абсолютного score на решение по марже. F2 доказала числом,
    # что голый top1_score не разделяет позитив/импостеров ни на одном
    # пороге без неприемлемой цены (0.55 -> FPR=100%; 0.9 -> FNR=66.7%;
    # EER 0.85 -> 8.9%/26.7%) — qa/scan-eval-runs/not-in-catalog-calibration/.
    # Правило контракта: not_in_catalog, если top1_score < CV_ABS_FLOOR ИЛИ
    # gap < CV_MARGIN_FLOOR (gap — та же величина, что и для near-dup routing
    # выше: отрыв от первого НЕ-той-же-группы конкурента).
    #
    # Исключение: успешный OCR (ocr_verified=True) ОБХОДИТ проверку по gap —
    # маленький gap в near-dup ветке ровно и означает "top1 близко к другой
    # позиции ТОЙ ЖЕ семьи", а верификатор существует именно для разрешения
    # этой неоднозначности содержательно (год/категория с этикетки), а не
    # числом. Считать результат неуверенным ПОСЛЕ того, как OCR уже дал
    # конкретный ответ, значило бы игнорировать более сильный сигнал ради
    # более слабого. Score-порог (CV_ABS_FLOOR) при этом всё равно
    # применяется и после OCR — уверенное распознавание этикетки не спасает
    # от в целом слабого ANN-совпадения (сюда OCR не проникает: он читает
    # текст, а не оценивает общее визуальное сходство).
    #
    # v0.4.7 (контракт §2, TODO-0 ревью 05, "null-gap = доминирование"):
    # `top.gap is None` НЕ провал маржи — маржинальная проверка СЧИТАЕТСЯ
    # ПРОЙДЕННОЙ (null означает "в top-K нет кандидата вне семьи top-1", то
    # есть top1 доминирует над всем, что вообще нашлось, а не "неизвестно,
    # есть ли конкурент"). not_in_catalog на null-gap возможен ТОЛЬКО через
    # первое условие (top1_score < CV_ABS_FLOOR). Эта ветка (`top.gap is
    # None`) технически уже была здесь до v0.4.7 (коммит f87768e, часть
    # исходной v0.4.5) — F3 полномасштабный baseline (reports/
    # f3-synthetic-baseline.md, b4f9608) намерил официальный match-rate 6,8%
    # при raw top-1 69,4% на ЭТОЙ ЖЕ формуле, что и раскрыло TODO-0. Разбор
    # ДО/ПОСЛЕ этой волны с разбивкой not_in_catalog по причине (floor vs
    # margin) — reports/b4-gate-v047.md §5 (конкретные цифры измерены там, не
    # предполагаются здесь). Явного регресс-теста на "null-gap + score>=floor
    # -> confident" при этом не было (test_scan_photo.py ни разу не
    # констролировал gap=None у top1) — закрыто этой волной:
    # test_null_gap_with_high_score_is_confident_not_margin_failure.
    confident = top.score >= settings.cv_abs_floor and (
        ocr_verified or top.gap is None or top.gap >= settings.cv_margin_floor
    )

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
