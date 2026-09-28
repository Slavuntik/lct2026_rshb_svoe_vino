"""Пайплайн /scan/photo (contracts/image-scan.md): ANN top-K -> отбор
кандидатов верификатора по близости скоров -> OCR-верификатор ->
confident/not_in_catalog решение по family-based марже.

Пороги — все ПЛЕЙСХОЛДЕРЫ до реальной калибровки на голд-сете кейса, но
разной степени "угаданности" (см. app/config.py, там же env-имена):
- CV_VERIFY_PROXIMITY — v0.4.8, стартово = CV_GROUP_EPSILON пакета
  packages/cv (0.03) — порог близости raw score для ОТБОРА КАНДИДАТОВ
  верификатора, полностью независим от family-based gap (см. ниже) и от
  переписи near-dup семей. Заменил CV_NEAR_DUP_GAP_THRESHOLD в этой роли —
  диагноз q2 ("Мускатель Массандра", reports/g4-family-gap.md): линейки
  ОДНОГО дизайна этикетки, но с разными названиями перепись не считает
  семьёй, и gap-based отбор кандидатов на такой паре схлопывался до одного
  top1, хотя весь топ-5 держался в пределах 0.033 по score.
  "Дополнения v0.4.9" (после живого e2e B5, reports/b5-gate-v048.md §2
  "Причина 1"): 0.03 -> 0.04 — на живом каталоге (case-20260917) сама цель
  q2 (massandra-muskatel-belyy-...) не попадала в кандидаты, промахнувшись
  мимо старого порога на 0,0029 (разрыв top1->цель 0,03287); 0.04 включает
  её, cap top-5 (см. run_photo_scan ниже) по-прежнему держит бюджет OCR.
- CV_NEAR_DUP_GAP_THRESHOLD — v0.4.8: БОЛЬШЕ НЕ используется этим модулем
  (заменён CV_VERIFY_PROXIMITY выше). Поле в app/config.py оставлено не
  удалённым ради обратной совместимости (кто-то мог выставить этот env
  снаружи) — README/отчёт см. reports/b5-gate-v048.md.
- CV_ABS_FLOOR — v0.4.5, реально откалиброван F2 на impostor-холдауте (45
  "чужих" вин против калибровочного индекса): чистый score-порог НЕ
  разделяет позитив/импостеров без неприемлемой цены, минимальный порог с
  FPR<=5% — 0.9 (ценой FNR=66.7%), см. qa/scan-eval-runs/
  not-in-catalog-calibration/report.md.
- CV_MARGIN_FLOOR — v0.4.5, добавлен КАК рычаг ПО ВЫВОДУ калибровки F2
  ("одного порога на сыром score недостаточно"). v0.4.7 перевёл `gap` на
  family-based метрику (G4) — другая шкала величин, старое число 0.25 с
  этой волны (v0.4.8) считается недействительным для новой шкалы;
  пересчёт — импостор-протоколом, отдельно оркестратором (не эта правка,
  см. reports/b5-gate-v048.md).

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

v0.4.11 (агент B8, разбор чата кейса 21.09): PhotoScanResult.candidates —
тот же top-5, что и matches, но КАЖДАЯ позиция обогащена карточкой
(name/winery_name/region_name/image_url/source_url) — из нашего каталога
(RAG), иначе из каталога кейса (`app/rag/case_catalog.py`, тот же фолбэк, что
и у `card`/GET /wines/{id}, см. app/rag/cards.py::build_wine_card). UI
показывает это поле при not_in_catalog=true ("Возможно, это одно из:") —
поле в ответе есть ВСЕГДА, ровно тот же принцип, что и у matches.

v0.4.12 (G5 `packages/cv/cv/text_rerank.py`, встраивание — agents/B9-text-
rerank-integration.md): при `settings.cv_text_rerank` — ОДИН проход OCR по
запросу (`verifier.read_query_text()`, тот же кроп `normalize_query()`, что
видит `ImageIndex.search()`) СРАЗУ после ANN, ДО near-dup routing ниже;
`cv.text_rerank.rerank_top_k()` переранжировывает top-K `matches` этим
текстом (текст кандидата — каталог кейса `app/rag/case_catalog.py`,
фолбэк — наш RAG-каталог, см. `_text_rerank_catalog_entry`) — меняется
ТОЛЬКО порядок списка `matches`, каждый `Match` несёт СВОИ исходные
score/gap/view (не blended-скор: пороги гейта CV_ABS_FLOOR/CV_MARGIN_FLOOR
ниже читают top.score/top.gap ТОГО матча, что теперь на позиции 0 — сама
калибровка порогов этой правкой не тронута). Тот же `ocr_text` передаётся
`verifier.verify()` (near-dup routing, см. ниже) — второго прохода OCR нет.
`ocr_text=None` (флаг выключен, дефолт) — `verify()` ведёт себя бит-в-бит
как раньше, читает OCR сам. Реордер `matches` происходит ДО того, как из
него строятся `candidates`/`_match_items`/`top`/`candidate_slugs` — flat
(best_guess_slug) и rich (slug/matches/candidates) поэтому автоматически
согласованы (один и тот же переранжированный список, контракт §v0.4.12 п.4).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from ..config import Settings
from ..rag import case_catalog as rag_case_catalog
from ..rag.cards import build_wine_card
from ..rag.interface import Retriever
from . import case_catalog, vision_llm
from .interface import ImageIndex, LabelVerifier, Match, VerifyCandidate

logger = logging.getLogger(__name__)


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
    candidates: list[dict] = field(default_factory=list)  # v0.4.11: top-5 позиций, обогащённых карточкой
    # CV_FUSION: чем читали этикетку ("vlm" | "ocr") и что прочитали — для архива сканов
    # и отладки; в HTTP-ответ не выводятся (контракт не меняется).
    identity_rejection: str | None = None
    model_label_fields: dict[str, str] = field(default_factory=dict)
    text_source: str | None = None
    label_text: str | None = None
    # Тимлид 22.09 (расширение брифа scan-budget, п.9, CV_FUSION_CHOOSE) — служебное
    # сравнение "локального" (CV+OCR, без модели) и "модельного" (текущая склейка)
    # ответов слияния, посчитанных на ОДНИХ И ТЕХ ЖЕ CV-векторах. НЕ в контракте
    # (routers/scan.py не читает эти поля в HTTP-ответ) — только архив/лог/офлайн-
    # анализ ml-lead. Все четыре None вне CV_FUSION (обычный путь run_photo_scan()
    # их не трогает — дефолты дataclass).
    local_slug: str | None = None  # top-1 локального (CV+OCR) слияния
    model_slug: str | None = None  # top-1 модельного (текущая склейка) слияния
    answers_agree: bool | None = None  # local_slug == model_slug (None — если какой-то из них None)
    chosen_answer_side: str | None = None  # "local" | "model" — какой ответ реально ушёл наружу


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


def _candidate_item(retriever: Retriever, slug: str, score: float) -> dict:
    """v0.4.11 (агент B8): один элемент `candidates` — карточка-сводка для
    слага, не голый {slug, score} (то — `_match_items` выше, для eval).
    Источник данных — НАША карточка (RAG) первым делом, иначе каталог кейса
    — ровно тот же порядок источников и та же честная деградация, что и
    `build_wine_card()` (app/rag/cards.py) для card/GET /wines/{id}:
    контракт требует ту же енричментную логику, не отдельную копию."""
    candidate = retriever.get_by_id(slug)
    if candidate is not None and candidate.kind == "wine":
        source = candidate.meta["source"]
        return {
            "wine_id": slug,
            # Хотфикс по прецеденту AnalogsWineItem (routers/scan.py, similar/
            # analogs): `.get("name", slug)` дефолтит ТОЛЬКО на отсутствующий
            # ключ, не на явный None (боевой каталог его несёт у единичных
            # позиций) — `ScanCandidateItem.name` обязателен, `or slug`
            # закрывает оба случая разом, без отдельного фильтра ниже по
            # стеку (в отличие от similar/analogs, кандидат никогда не
            # отбрасывается — это тот же список, что matches, порядок и
            # длина обязаны совпадать 1:1).
            "name": source.get("name") or slug,
            "winery_name": source.get("winery_name") or None,
            "region_name": source.get("region_name") or None,
            "image_url": source.get("image_url"),
            "source_url": candidate.url,
            "score": score,
        }

    case_wine = rag_case_catalog.lookup(slug)
    if case_wine is not None:
        return {
            "wine_id": slug,
            "name": case_wine.name,
            "winery_name": case_wine.winery_name or None,
            "region_name": case_wine.region_name or None,
            "image_url": rag_case_catalog.thumb_url(slug),
            "source_url": rag_case_catalog.source_url(slug),
            "score": score,
        }

    # Честная деградация: слаг не резолвится НИГДЕ (ни наш RAG, ни каталог
    # кейса не знают его) — тот же принцип, что и в _verify_candidates ниже:
    # не роняем rich-ответ, отдаём голый slug вместо имени. source_url всё
    # равно строим по конвенции "Своего Вина" — приватная проверка кейса
    # содержит ТОЛЬКО вина из каталога кейса (case.md), так что CV в
    # принципе не должен находить слаги вне его — этот путь чисто защитный.
    return {
        "wine_id": slug, "name": slug, "winery_name": None, "region_name": None,
        "image_url": None, "source_url": rag_case_catalog.source_url(slug), "score": score,
    }


def _candidate_items(retriever: Retriever, matches: list[Match], limit: int = 5) -> list[dict]:
    """v0.4.11 (контракт §1): top-5 схлопнутых позиций, обогащённых карточкой
    — присутствует в rich-ответе ВСЕГДА (не только при not_in_catalog — тот
    же принцип, что и у `matches`/v0.4.3: UI решает, когда показывать,
    бэкенд не скрывает данные заранее). `matches` уже схлопнуты в позиции
    самим ImageIndex.search() — здесь только берём top-`limit` как есть и
    обогащаем каждую карточкой."""
    return [_candidate_item(retriever, m.slug, m.score) for m in matches[:limit]]


def _verify_candidates(retriever: Retriever, slugs: list[str]) -> list[VerifyCandidate]:
    """v0.4.4: LabelVerifier.verify() принимает метаданные из каталога, не
    голые slug'и — "B передаёт метаданные кандидатов из каталога (get_by_id)".

    "Дополнения v0.4.9" (после e2e B5, reports/b5-gate-v048.md §2 "Причина
    3"): источник метаданных ПЕРВЫМ делом — КАТАЛОГ КЕЙСА
    (`case_catalog.lookup()`, case-data/slug_refs.json), не наш RAG/wines-
    каталог. Кейс-слаги в нашем каталоге отсутствуют вовсе, а mock-RAG (dev/
    тестовый профиль) отдаёт латиницу/транслит — бесполезно для
    кириллических словарей верификатора (packages/cv/cv/verify.py::
    _WINE_TYPE_KEYWORDS/_COLOR_KEYWORDS, граница слова): живой q2 e2e у B5
    получил `matched_on: []` на всех кандидатах ровно по этой причине.
    `case_catalog.lookup()` сам деградирует в `None`, когда case-data не
    приехали на этой машине/slug ей не известен — тогда, и только тогда,
    используется ПРЕЖНИЙ путь `retriever.get_by_id()`: каталог может не
    знать конкретный slug (CV нашёл позицию, которой ещё/уже нет в
    каталожном слое — тот же реалистичный сценарий рассинхрона, что и у
    `card=None`) — тогда деградируем честно: name=slug, vintage=None, а не
    падаем и не роняем near-dup routing из-за пробела в каталоге."""
    result: list[VerifyCandidate] = []
    for slug in slugs:
        case_meta = case_catalog.lookup(slug)
        if case_meta is not None:
            result.append(VerifyCandidate(slug=slug, name=case_meta.name, vintage=case_meta.vintage))
            continue
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
    retriever: Retriever, slugs: Iterable[str], best_slug: str
) -> tuple[list[dict], list[dict]]:
    """`slugs` — тот же top-K, что и `matches`/`candidates`, просто голые slug'и
    (v0.4.13, CV_FUSION: слияние строит свой ранжированный список кандидатов,
    не `list[Match]` — см. `_run_photo_scan_fusion` ниже; сигнатура обобщена на
    `Iterable[str]`, вызывающий код без слияния передаёт `m.slug for m in matches`
    как раньше, поведение не меняется)."""
    similar: list[dict] = []
    for slug in _dedupe_preserve_order(slugs):
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


# --------------------------------------------------------------------------
# v0.4.12: текстовое переранжирование top-K (G5 packages/cv/cv/text_rerank.py,
# встраивание — agents/B9-text-rerank-integration.md). Ленивый импорт
# `cv.text_rerank` (не на верху модуля) — тот же принцип, что `app/cv/
# factory.py` уже применяет к `cv.index`/`cv.verify`: apps/api НЕ требует
# тяжёлый пакет packages/cv (torch/paddleocr транзитивно через его
# pyproject.toml) в базовой установке, пока CV_TEXT_RERANK=0 (дефолт) — ни
# один тест базового свода этот импорт не трогает вовсе.
# --------------------------------------------------------------------------


def _import_text_rerank():
    try:
        from cv import text_rerank  # packages/cv, зона G5/G6 — только читаем отсюда
    except ImportError as exc:
        raise RuntimeError(
            "CV_TEXT_RERANK=1, но пакет packages/cv не установлен в это "
            "окружение (uv sync --extra integration в apps/api). Пока не "
            "нужен — оставьте CV_TEXT_RERANK=0 (дефолт)."
        ) from exc
    return text_rerank


@lru_cache(maxsize=8)
def _text_rerank_idf(case_data_dir: str):
    """IDF по ВСЕМУ каталогу кейса (`case_catalog.json`, `app/rag/
    case_catalog.py::all_slugs()`) — корпусная статистика "редкий токен vs
    стоп-слово-подобный" нужна на большом словаре, не на 5-10 кандидатах
    одного запроса (см. `cv.text_rerank.build_idf`/`has_distinctive_token`).
    Кэш ключуется строкой `case_data_dir` (тот же паттерн, что `app/rag/
    case_catalog.py::_load_catalog(path_str)` и `app/cv/case_catalog.py::
    _load_mapping(path_str)`) — вызывающий код передаёт `str(rag_case_catalog.
    case_data_dir())`, так что разные `CASE_DATA_DIR` в разных тестах не
    видят чужой кэш. Пустой/отсутствующий каталог -> пустой словарь IDF ->
    `text_score()` отдаёт 0.0 для всех (каталог тоже пуст) — переранжирование
    честно вырождается в no-op, не падает (см. `all_slugs()`)."""
    text_rerank = _import_text_rerank()
    catalog: dict[str, object] = {}
    for slug in rag_case_catalog.all_slugs():
        wine = rag_case_catalog.lookup(slug)
        if wine is None:
            continue
        catalog[slug] = text_rerank.CatalogText(
            slug=slug, name=wine.name, winery=wine.winery_name,
            grape=" ".join(wine.grapes), region=wine.region_name,
        )
    return text_rerank.build_idf(catalog)


def _text_rerank_catalog_entry(retriever: Retriever, slug: str, text_rerank_module):
    """Текст ОДНОГО кандидата для переранжирования — контракт §v0.4.12 п.3:
    каталог кейса (`app/rag/case_catalog.py`, name/winery_name/grapes/
    region_name) ПЕРВЫМ делом, фолбэк — наш каталог (RAG, `retriever.
    get_by_id()`) — ровно тот же порядок источников, что уже использует
    `_candidate_item()` выше для карточки `candidates` (v0.4.11). Слаг,
    неизвестный НИГДЕ, — честная деградация: `CatalogText` с пустыми полями
    (`text_score()` тогда отдаёт 0.0 для него — не участвует в переранжировании,
    не роняет остальных кандидатов)."""
    case_wine = rag_case_catalog.lookup(slug)
    if case_wine is not None:
        return text_rerank_module.CatalogText(
            slug=slug, name=case_wine.name, winery=case_wine.winery_name,
            grape=" ".join(case_wine.grapes), region=case_wine.region_name,
        )
    candidate = retriever.get_by_id(slug)
    if candidate is not None and candidate.kind == "wine":
        source = candidate.meta["source"]
        grapes = source.get("grapes")
        grape_text = " ".join(grapes) if isinstance(grapes, list) else (grapes or "")
        return text_rerank_module.CatalogText(
            slug=slug,
            name=source.get("name") or "",
            winery=source.get("winery_name") or "",
            grape=grape_text,
            region=source.get("region_name") or "",
        )
    return text_rerank_module.CatalogText(slug=slug)


def _apply_text_rerank(
    matches: list[Match], ocr_text: str, retriever: Retriever, settings: Settings
) -> list[Match]:
    """contracts/image-scan.md v0.4.12: `cv.text_rerank.rerank_top_k()` по
    top-`CV_TEXT_RERANK_K` схлопнутых `matches`, вес `CV_TEXT_RERANK_W`,
    безопасный гейт `min_token_idf` (медиана IDF каталога, рекомендация G5 —
    см. `cv.text_rerank.distinctive_idf_threshold`). Меняет ТОЛЬКО порядок —
    возвращает те же объекты `Match` (score/gap/view нетронуты), просто
    переставленные; пустой `ocr_text` -> `rerank_top_k` возвращает вход как
    есть (text_score=0 для всех, стабильная сортировка не меняет порядок,
    см. докстринг `rerank_top_k`)."""
    if not matches:
        return matches
    text_rerank_module = _import_text_rerank()
    idf = _text_rerank_idf(str(rag_case_catalog.case_data_dir()))
    catalog = {
        m.slug: _text_rerank_catalog_entry(retriever, m.slug, text_rerank_module)
        for m in matches[: settings.cv_text_rerank_k]
    }
    ranked = [(m.slug, m.score) for m in matches]
    reranked = text_rerank_module.rerank_top_k(
        ranked, ocr_text, catalog, idf,
        k=settings.cv_text_rerank_k, w=settings.cv_text_rerank_w,
        min_token_idf=text_rerank_module.distinctive_idf_threshold(idf),
    )
    by_slug = {m.slug: m for m in matches}
    return [by_slug[slug] for slug, _score in reranked]


# --------------------------------------------------------------------------
# CV_FUSION (agents/G7-text-fusion.md): боевое слияние CV (кроп + весь кадр) +
# текстовый поиск по ВСЕМУ каталогу кейса (cv/text_fusion.py) — независимый путь
# от _apply_text_rerank выше (contracts/image-scan.md не трогается; см. брифа
# "Не делать" — "контракт не правь"). Ленивый импорт `cv.text_fusion`/`cv.families`
# — тот же принцип, что `_import_text_rerank()`: apps/api не должен требовать
# packages/cv (torch/paddleocr транзитивно), пока CV_FUSION=0 (дефолт).
# --------------------------------------------------------------------------


def _import_cv_fusion_deps():
    try:
        from cv import families as cv_families
        from cv import text_fusion
        from cv import text_rerank as tr
    except ImportError as exc:
        raise RuntimeError(
            "CV_FUSION=1, но пакет packages/cv не установлен в это окружение "
            "(uv sync --extra integration в apps/api). Пока не нужен — оставьте "
            "CV_FUSION=0 (дефолт)."
        ) from exc
    return cv_families, text_fusion, tr


@lru_cache(maxsize=4)
def _fusion_text_index(catalog_csv: str):
    """`cv.text_fusion.TextIndexV2` по ВСЕМУ каталогу CSV кейса — строится один
    раз на процесс на каждый путь (см. докстринг `cv.text_fusion.load_catalog_index`
    — тот же паттерн ключевания строкой, что `_text_rerank_idf` ниже/выше:
    разные `CV_CASE_CATALOG_CSV`/`CASE_DATA_DIR` в разных тестах не видят чужой
    кэш)."""
    _, text_fusion, _ = _import_cv_fusion_deps()
    return text_fusion.load_catalog_index(catalog_csv)


@lru_cache(maxsize=4)
def _fusion_colors(catalog_csv: str):
    """slug -> цвет вина по каталогу кейса — для штрафа за противоречие цвета этикетки
    (`cv.text_fusion.fuse(colors=..., color_penalty=...)`); тот же кэш по пути CSV, что у индексов."""
    _, text_fusion, _ = _import_cv_fusion_deps()
    return text_fusion.color_by_slug(_fusion_text_index(catalog_csv))


@lru_cache(maxsize=4)
def _fusion_winery_index(catalog_csv: str, aliases_json: str):
    """agents/H1-cpu-path.md: `cv.text_fusion.TextIndexV2` ТОЛЬКО по полю `winery` —
    для гейта «не подтверждена винодельня» (`cv.text_fusion.fuse(winery_index=...)`).
    ОТДЕЛЬНЫЙ индекс/кэш от `_fusion_text_index` выше (тот несёт `FUSION_FIELDS`
    целиком — name+winery+grape+category+sugar, непригоден для recall ИМЕННО поля
    winery) — тот же паттерн ключевания строкой по `catalog_csv`, тот же честный
    выход на отсутствующий CSV (пустой индекс, см. `load_catalog_index`).

    agents/ML-1-*.md (задача 2, 22.09): делегирует `cv.text_fusion.load_winery_index()`
    — ОБЪЕДИНЯЕТ доктокены слагов, чья строка «Винодельня» входит в проверенную
    вручную группу алиасов (`case-data/winery_aliases.json`, путь резолвит
    вызывающий код через `text_fusion.default_winery_aliases_path()`, тот же живой
    резолв env, что `cv.families.default_families_path()`). Кэш ключуется ОБОИМИ
    путями (catalog_csv, aliases_json) — разные CASE_DATA_DIR/CV_WINERY_ALIASES_JSON
    в разных тестах не видят чужой кэш, тот же принцип, что `_fusion_family_by_slug`
    ниже. Файл алиасов отсутствует/пуст -> честная деградация к прежнему 1:1
    (см. докстринг `load_winery_index`)."""
    _, text_fusion, _ = _import_cv_fusion_deps()
    return text_fusion.load_winery_index(catalog_csv, aliases_json=aliases_json)


@lru_cache(maxsize=4)
def _fusion_family_by_slug(families_json: str):
    """near-dup семьи переписи (`case-data/families.json`) для family-based
    `gap` слияния (`cv.text_fusion._family_gap`) — ТА ЖЕ перепись, что `cv/
    index.py::ImageIndex._get_family_by_slug()` использует для обычного `Match.
    gap` (см. `cv/families.py`), загружена отдельно здесь: `fuse()` работает на
    голых `{slug: score}`, не на `ImageIndex`, и не имеет доступа к приватному
    кэшу конкретного инстанса индекса."""
    cv_families, _, _ = _import_cv_fusion_deps()
    return cv_families.load_family_by_slug(Path(families_json))


# Тимлид 22.09 (расширение брифа scan-budget, п.7): ДВА РАЗНЫХ пула — OCR (CPU-
# работа: read_query_text()/verify()) и модели (сетевые запросы к VLM-шлюзу/
# локальному серверу). Раньше был один общий `_FUSION_TEXT_POOL` — зависший
# запрос к шлюзу (см. `_read_response_within_deadline` в vision_llm.py:
# "медленная выдача" не ловится сокет-таймаутом) мог занять поток, которого
# ждёт read_query_text() СЛЕДУЮЩЕГО запроса (тот же процесс, тот же пул) —
# теперь это два независимых ресурса, сетевой затор не крадёт поток у CPU-OCR.
_FUSION_OCR_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fusion-ocr")
_FUSION_MODEL_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fusion-model")


class _ModelBreaker:
    """Предохранитель на ОДНУ модель ("vlm" или "vlm_local" — см. `_MODEL_BREAKERS`
    ниже, свой экземпляр на каждую) — тимлид 22.09, расширение брифа scan-budget
    (п.8, решение Вячеслава: "любые сбои пользователь не замечает ни ошибкой, ни
    задержкой"). `VISION_LLM_BREAKER_FAILS` сбоев/таймаутов ПОДРЯД -> открыт на
    `VISION_LLM_BREAKER_COOLDOWN_S` секунд: `allow()` возвращает False, скан этой
    модели вообще не пробует (не тратит поток `_FUSION_MODEL_POOL`, не ждёт
    дедлайн) — сразу идёт локальным путём. Пауза истекла -> РОВНО один пробный
    скан (см. `allow()`): успех (`record_success`) закрывает предохранитель,
    неудача (`record_failure`) открывает заново на новый cooldown.

    "Ровно один" пробный скан под конкуренцией: первый вызов `allow()` ПОСЛЕ
    истечения паузы сам, атомарно под локом, ОТОДВИГАЕТ `_opened_until` вперёд
    (как если бы уже переоткрылся) — конкурентные вызовы в ту же миллисекунду
    видят предохранитель всё ещё "открытым" и не лезут пробовать модель тоже;
    исход пробного скана подтверждает (закрывает) или обновляет (переоткрывает)
    это временное состояние. Потокобезопасен (свой `Lock` — сканы разных
    запросов работают из разных потоков `_FUSION_MODEL_POOL`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_until: float | None = None

    def allow(self, *, cooldown_s: float) -> bool:
        with self._lock:
            if self._opened_until is None:
                return True
            now = time.monotonic()
            if now < self._opened_until:
                return False
            self._opened_until = now + cooldown_s  # пробный скан — см. докстринг класса
            return True

    def record_success(self, *, name: str) -> None:
        with self._lock:
            was_tripped = self._consecutive_failures > 0 or self._opened_until is not None
            self._consecutive_failures = 0
            self._opened_until = None
        if was_tripped:
            logger.info("cv_fusion: предохранитель модели %s закрыт — успешный ответ", name)

    def record_failure(self, *, name: str, fails_threshold: int, cooldown_s: float) -> None:
        with self._lock:
            self._consecutive_failures += 1
            trip = self._consecutive_failures >= fails_threshold
            failures = self._consecutive_failures
            if trip:
                self._opened_until = time.monotonic() + cooldown_s
        if trip:
            logger.warning(
                "cv_fusion: предохранитель модели %s открыт на %.0fс после %d сбоев/таймаутов подряд",
                name, cooldown_s, failures,
            )


_MODEL_BREAKERS: dict[str, _ModelBreaker] = {"vlm": _ModelBreaker(), "vlm_local": _ModelBreaker()}


def _reset_model_breakers() -> None:
    """Только для тестов — предохранители держат состояние НА ПРОЦЕСС (модульный
    словарь), между тестами его нужно явно сбрасывать (тот же приём, что
    `_fusion_text_index.cache_clear()` и соседи в tests/test_scan_photo_fusion.py)."""
    for breaker in _MODEL_BREAKERS.values():
        with breaker._lock:
            breaker._consecutive_failures = 0
            breaker._opened_until = None


def _verify_with_budget(
    verifier: LabelVerifier, image_bytes: bytes, candidates: list[VerifyCandidate],
    ocr_text: str | None, deadline: float,
) -> str | None:
    """Near-dup `verify()` — СВОЙ OCR-проход (`packages/cv/cv/verify.py`), раньше БЕЗ
    какого-либо таймаута вовсе — тимлид 22.09, находка при расширении брифа
    scan-budget: rich-фото `41.8_22-08-2026_20-56-40.webp` заняло 14.6с на сервере
    против 3.4с у flat того же фото; `verify()` — общий шаг ДЛЯ ОБОИХ режимов
    ответа (flat и rich зовут один и тот же `run_photo_scan()`), ничем не
    ограниченный раньше — реальный кандидат в объяснение разрыва (наравне с
    совпадением по времени с параллельным запросом чата, см. отчёт).

    Отправляется в `_FUSION_OCR_POOL` (тот же ресурс, что `read_query_text()` —
    тоже CPU-OCR, не сеть — см. `_fusion_text_and_vectors`) и ждётся не дольше
    остатка `deadline` — ТОГО ЖЕ бюджета (`t0 + CV_SCAN_BUDGET_S`), что и OCR
    текстовой ветки: "весь запрос", не отдельный шаг со своим бюджетом. Не
    успел/бросил исключение -> `None`, WARNING без содержимого (ни фото, ни
    текста, ни кандидатов) — вызывающий код остаётся на top-1 ANN/слияния БЕЗ
    OCR-верификации (контракт уже предусматривает `None` как честный исход
    "не смог разлить", см. докстринг `LabelVerifier.verify()`). Поток НЕ
    отменяется (Python не умеет прервать блокирующий вызов в чужом потоке) —
    доработает в фоне пула, тот же риск/компромисс, что у OCR текстовой ветки
    (см. reports/ml-eng-scan-budget.md, оценка "съедает ли ядра")."""
    fut = _FUSION_OCR_POOL.submit(verifier.verify, image_bytes, candidates, ocr_text=ocr_text)
    try:
        return fut.result(timeout=max(0.0, deadline - time.monotonic()))
    except Exception:  # noqa: BLE001 — не успел к бюджету/сбой потока: честно "не смог разлить"
        logger.warning("cv_fusion: near-dup verify() не уложился в бюджет — кандидат остаётся без OCR-верификации")
        return None


def _no_bottle_signal(bottle_flags: dict[str, bool | None]) -> bool:
    """ml-lead/тимлид 27.09 (reports/ml-eng-not-a-bottle.md): агрегирует
    `bottle_visible` ОТВЕТИВШИХ моделей (vlm/vlm_local, см. `vision_llm.
    read_label_fields_or_raise`) в один сигнал "модель прямо говорит, что
    бутылки/этикетки на кадре нет". `True` — ТОЛЬКО когда хотя бы одна модель
    ДАЛА булево значение поля И ни одна из ответивших не сказала `True`
    (осторожность с ценой ошибки, бриф тимлида п.2: расхождение "vlm говорит
    нет, vlm_local говорит да" — НЕ сигнал, слишком похоже на ошибку одной из
    моделей, не на честный кадр без бутылки). Модели, которые не ответили
    вовсе (таймаут/сбой/предохранитель/не настроены — `None` в словаре),
    участия не принимают ни за, ни против — тот же принцип, что и остальные
    поля PROMPT (пустой-но-честный ответ отличается от сбоя)."""
    votes = [v for v in bottle_flags.values() if v is not None]
    return bool(votes) and all(v is False for v in votes)


def _fusion_text_and_vectors(
    image_bytes: bytes, image_index: ImageIndex, verifier: LabelVerifier, settings: Settings,
    t0: float | None = None,
) -> tuple[str, str, str, tuple[list[float], list[float]], bool]:
    """(текст для слияния, источник, текст OCR, CV-векторы, no_bottle_signal) —
    всё параллельно. `no_bottle_signal` (27.09, вето "не бутылка" — см.
    `_no_bottle_signal()` выше) — `True`, когда модель(и), реально ответившая
    в ЭТОМ запросе, прямо сообщила `bottle_visible=false`; вызывающий код
    (`_run_photo_scan_fusion`) решает, применять ли вето, ПО ДАННЫМ (порог
    `settings.cv_not_a_bottle_cv_ceiling`), эта функция только несёт сигнал.

    PaddleOCR/RapidOCR читается всегда (фолбэк и вход near-dup верификатора). Модели — по
    `CV_FUSION_TEXT_SOURCE`: "vlm" — GPU-сервер (шлюз), "vlm_local" — локальная MLX-модель,
    "vlm_both" — обе, их тексты склеиваются (замер: обе вместе 96.8% против 95.2% у каждой).
    CV-эмбеддинги считаются в этом же потоке, пока текст читается. Источник в ответе:
    "vlm", "vlm_local", "vlm_both" (ответили обе) или "ocr".

    Тимлид 22.09 (страховка лимита 10с приватной проверки, reports/devops-hack-v13.md:
    хвост p50/p95/max 4.4/6.6/9.1с на стенде 4 vCPU) + расширение (решение Вячеслава:
    "модель и локальный путь стартуют одновременно; модель не ответила за 6 с — отдаём
    локальный ответ; ответила — выбираем лучший; любые сбои пользователь не замечает ни
    ошибкой, ни задержкой"):

    - `CV_SCAN_BUDGET_S` (`settings.cv_scan_budget_s`) — бюджет ожидания OCR, ОТ НАЧАЛА
      ОБРАБОТКИ ЗАПРОСА (`t0`, передан вызывающим `run_photo_scan()`), НЕ от входа в эту
      функцию (раньше `ocr_text = f_ocr.result()` ждала БЕЗ таймаута вовсе — реальный
      риск зависания сверх лимита 10с под конкуренцией за CPU). `t0=None` (юниты, зовущие
      эту функцию напрямую) — старое поведение, дедлайн от входа в функцию.
    - `VISION_LLM_TIMEOUT_S` (`settings.vision_llm_timeout_s`, дефолт 6.0) — дедлайн
      КАЖДОЙ модели, ТОЖЕ от `t0` (раньше считался от входа в функцию — терял время,
      уже потраченное на шаги ДО текстовой ветки).
    - Свой предохранитель НА КАЖДУЮ модель (`_MODEL_BREAKERS`, см. `_ModelBreaker`) —
      `VISION_LLM_BREAKER_FAILS` сбоев/таймаутов подряд открывают его на
      `VISION_LLM_BREAKER_COOLDOWN_S`: следующие сканы пропускают ЭТУ модель БЕЗ отправки
      запроса вовсе (не тратят дедлайн, не занимают поток `_FUSION_MODEL_POOL`).
    - OCR и модели читаются РАЗНЫМИ пулами (`_FUSION_OCR_POOL`/`_FUSION_MODEL_POOL`) —
      зависший запрос к шлюзу не крадёт поток у CPU-OCR следующего скана.
    - Любое исключение/таймаут ЛЮБОГО источника — WARNING без содержимого фото/текста,
      наружу ничего не бросается: не успел OCR -> текст модели, если она ответила,
      иначе только CV (`label_text=""`); не ответила ни одна модель -> текст OCR (как
      раньше). Поток, который мы перестали ждать, НЕ отменяется (Python не умеет
      прервать блокирующий вызов в чужом потоке) — продолжает работать в фоне пула,
      см. `reports/ml-eng-scan-budget.md` про оценку риска "съедает ли ядра следующего
      запроса" и предложенную меру.

    agents/ML-1-*.md (задача 1): `settings.cv_fusion_merge_model_text` (дефолт
    выключен) — когда включён И хотя бы одна модель ответила, OCR ДОБАВЛЯЕТСЯ к
    тексту модели(ей) через пробел, а не служит только фолбэком на случай "не
    ответила НИ ОДНА модель" (та ветка ниже не меняется). Флаг не влияет на
    `source` и не влияет на `ocr_text` — третий элемент кортежа, отдельное поле,
    которое видит near-dup verify()."""
    mode = settings.cv_fusion_text_source
    effective_t0 = time.monotonic() if t0 is None else t0
    scan_deadline = effective_t0 + settings.cv_scan_budget_s
    model_deadline = effective_t0 + settings.vision_llm_timeout_s
    fails_threshold = settings.vision_llm_breaker_fails
    cooldown_s = settings.vision_llm_breaker_cooldown_s

    readers: dict[str, object] = {}
    if (
        mode in ("vlm", "vlm_both") and settings.vision_llm_url and settings.vision_llm_key
        and _MODEL_BREAKERS["vlm"].allow(cooldown_s=cooldown_s)
    ):
        readers["vlm"] = _FUSION_MODEL_POOL.submit(
            vision_llm.read_label_fields_or_raise, image_bytes,
            url=settings.vision_llm_url, key=settings.vision_llm_key, model=settings.vision_llm_model,
            timeout_s=settings.vision_llm_timeout_s, image_size=settings.vision_llm_image_size,
        )
    if (
        mode in ("vlm_local", "vlm_both") and settings.vision_llm_local_url
        and _MODEL_BREAKERS["vlm_local"].allow(cooldown_s=cooldown_s)
    ):
        readers["vlm_local"] = _FUSION_MODEL_POOL.submit(
            vision_llm.read_label_fields_or_raise, image_bytes,
            url=settings.vision_llm_local_url, key=None, model=settings.vision_llm_local_model,
            timeout_s=settings.vision_llm_timeout_s, image_size=settings.vision_llm_image_size,
        )
    f_ocr = _FUSION_OCR_POOL.submit(verifier.read_query_text, image_bytes)
    vectors = image_index.embed_fusion_query(image_bytes)  # ValueError на битых байтах — как раньше
    try:
        ocr_text = f_ocr.result(timeout=max(0.0, scan_deadline - time.monotonic()))
    except Exception:  # noqa: BLE001 — не успел к бюджету/сбой потока: сливаем без OCR
        logger.warning(
            "cv_fusion: OCR не уложился в CV_SCAN_BUDGET_S=%.1fс (с начала запроса прошло %.2fс) "
            "— ответ без OCR-текста",
            settings.cv_scan_budget_s, time.monotonic() - effective_t0,
        )
        ocr_text = ""

    got: dict[str, str] = {}
    model_status = {}
    bottle_flags: dict[str, bool | None] = {}
    for name, fut in readers.items():
        try:
            text, bottle_visible = fut.result(timeout=max(0.0, model_deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 — не успела к дедлайну/сбой потока/HTTP-ошибка шлюза
            _MODEL_BREAKERS[name].record_failure(name=name, fails_threshold=fails_threshold, cooldown_s=cooldown_s)
            text, bottle_visible = "", None
            model_status[name] = "error_or_deadline"
        else:
            model_status[name] = "text" if text.strip() else "empty"
            _MODEL_BREAKERS[name].record_success(name=name)  # пустой, но ЧЕСТНЫЙ ответ — не сбой
        bottle_flags[name] = bottle_visible
        if text.strip():
            got[name] = text
    logger.info("cv_fusion_sources: models=%s", model_status)
    no_bottle_signal = _no_bottle_signal(bottle_flags)
    if not got:
        return ocr_text, "ocr", ocr_text, vectors, no_bottle_signal
    source = "vlm_both" if len(got) == 2 else next(iter(got))
    model_text = " ".join(got[k] for k in ("vlm", "vlm_local") if k in got)
    label_text = f"{model_text} {ocr_text}".strip() if settings.cv_fusion_merge_model_text else model_text
    # Mixed model readings need an explicit consensus policy before a name veto.
    fields = getattr(next(iter(got.values())), "fields", {}) if len(got) == 1 else {}
    return vision_llm.LabelText(label_text, fields), source, ocr_text, vectors, no_bottle_signal


def _choose_fusion_result(model_result, local_result, mode: str) -> tuple[object, str]:
    """(результат, "model"|"local") — тимлид 22.09, расширение брифа scan-budget
    (п.9, `CV_FUSION_CHOOSE`, решение Вячеслава: "модель и локальный путь стартуют
    одновременно ... ответила — выбираем лучший"). `model_result`/`local_result` —
    `cv.text_fusion.FusionResult`, посчитанные `_run_photo_scan_fusion()` на ОДНИХ
    И ТЕХ ЖЕ CV-векторах/кандидатной вселенной, разным текстом (модельный/склеенный
    против чистого OCR) — см. её докстринг.

    `mode="merge"` (дефолт) — ответ БИТ В БИТ как раньше (`model_result`),
    НЕЗАВИСИМО от `local_result` (тот всё равно посчитан вызывающим кодом — для
    сравнения в архиве/логе/офлайн-анализа ml-lead, просто не влияет на выбор, пока
    дефолт не переключён). Пустой `ranked` с одной из сторон (кандидатная
    вселенная `fuse()` не может быть пустой, пока `cv_scores` непуст — вызывающий
    код это уже проверил до вызова, но защита остаётся на случай будущих
    изменений) — эта сторона просто проигрывает.

    `mode="confident_else_cv"` (ml-lead, параллельный офлайн-разбор 22.09,
    `reports/ml-lead-choose-rule.md`, `qa/real_photos_choose_rule.py` — 62 живых
    фото каталога + 38 честных NONE, боевая `cv.text_fusion.fuse()`): доверяем
    модели, ТОЛЬКО если её СОБСТВЕННЫЙ `fuse()`-результат сам проходит уже
    откалиброванный гейт уверенности (`model_result.confident` — тот же
    `gap>=CV_FUSION_GAP_FLOOR И cv_score>=CV_FUSION_CV_FLOOR`, что решает
    `not_in_catalog` ниже по конвейеру), иначе локальный. Независимо от
    согласия/несогласия слагов — НЕ путать с `agree_else_*` ниже. На их выборке:
    не хуже merge на чистых 60/62, заметно устойчивее галлюцинации чтения модели
    (перестановка/похожая ниша между фото) на каждой проверенной точке 10/30/100%.
    Рекомендация ml-lead тимлиду — дефолт НЕ переключаю сам (решение "в бой" не
    моя зона), только добавляю режим."""
    if mode == "merge" or not local_result.ranked:
        return model_result, "model"
    if not model_result.ranked:
        return local_result, "local"
    if mode == "max_score":
        if local_result.ranked[0].final_score > model_result.ranked[0].final_score:
            return local_result, "local"
        return model_result, "model"
    if mode == "confident_else_cv":
        return (model_result, "model") if model_result.confident else (local_result, "local")
    if local_result.ranked[0].slug == model_result.ranked[0].slug:
        return model_result, "model"  # согласны — форма ответа от модельной стороны, слаг тот же
    if mode == "agree_else_cv" and local_result.ranked[0].cv_score > model_result.ranked[0].cv_score:
        return local_result, "local"
    return model_result, "model"  # agree_else_llm (расхождение) И неизвестный mode — честный фолбэк


def _run_photo_scan_fusion(
    *,
    image_bytes: bytes,
    image_index: ImageIndex,
    verifier: LabelVerifier,
    retriever: Retriever,
    settings: Settings,
    top_k: int,
    t0: float,
) -> PhotoScanResult:
    """contracts/image-scan.md НЕ описывает CV_FUSION (флаг вне контракта, см.
    reports/g7-text-fusion.md "Предложения к контракту") — эта ветка ПОЛНОСТЬЮ
    заменяет обычный путь `run_photo_scan` (ANN-топ / near-dup-by-proximity /
    _apply_text_rerank) своей собственной логикой, когда `settings.cv_fusion`
    истинно; `cv_text_rerank` в этом случае НЕ применяется — две независимые
    формулы поверх одного top1 не имеют согласованного смысла вместе (brief
    G7, задача 3: "если включены оба — действует слияние").

    OCR читается РОВНО ОДИН раз за запрос (`verifier.read_query_text()`, тот
    же принцип дисциплины, что v0.4.12) и переиспользуется и текстовым индексом
    (`cv.text_fusion.fuse()`), и near-dup верификатором ниже (`CV_FUSION_VERIFY`).
    """
    cv_families, text_fusion, tr = _import_cv_fusion_deps()

    label_text, text_source, ocr_text, vectors, model_no_bottle = _fusion_text_and_vectors(
        image_bytes, image_index, verifier, settings, t0,
    )
    text_index = _fusion_text_index(str(tr.default_catalog_csv_path()))
    winery_index = _fusion_winery_index(
        str(tr.default_catalog_csv_path()), str(text_fusion.default_winery_aliases_path()),
    )
    family_by_slug = _fusion_family_by_slug(str(cv_families.default_families_path()))

    # Тимлид 22.09 (расширение брифа scan-budget, п.9): кандидатная вселенная —
    # ОБЪЕДИНЕНИЕ текстовых extra_slugs ОБОИХ текстов (модельного/склеенного
    # `label_text` И чистого `ocr_text`), не только модельного, как раньше — иначе
    # "локальный" ответ ниже (fuse() с ocr_text) мог бы не видеть кандидата,
    # которого нашёл ТОЛЬКО OCR-текст (search_fusion() уже вызывается один раз,
    # на "тех же векторах", а не дважды — решение Вячеслава).
    text_top_model = text_fusion.text_top_slugs_for_ocr(text_index, label_text, text_fusion.DEFAULT_TEXT_TOP_N)
    text_top_local = (
        text_fusion.text_top_slugs_for_ocr(text_index, ocr_text, text_fusion.DEFAULT_TEXT_TOP_N) if ocr_text else []
    )
    extra_slugs = _dedupe_preserve_order([*text_top_model, *text_top_local])
    # v0.4.13 (brief G7 п.2): CV-скор — max(нормализованный кроп, весь кадр),
    # кандидаты — CV top-K ∪ текстовые extra_slugs (точным фильтрованным
    # запросом Qdrant, не ANN-топ — см. `ImageIndex.search_fusion()`).
    fusion_matches = image_index.search_fusion(
        None, top_k=text_fusion.DEFAULT_ANN_TOP_K, extra_slugs=extra_slugs, vectors=vectors,
    )

    if not fusion_matches:
        # "CV обязателен, OCR — усилитель" (contracts/image-scan.md) — CV не
        # нашла вообще ничего (индекс пуст/битый) -> честный not_in_catalog,
        # текст сам по себе кандидатов не создаёт (см. cv.text_fusion.fuse()).
        return PhotoScanResult(
            best_guess_slug=None, slug=None, card=None, top1_score=None, gap=None,
            ocr_verified=False, not_in_catalog=True,
            timing_ms=int((time.monotonic() - t0) * 1000),
            matches=[], candidates=[],
        )

    cv_scores = {m.slug: m.score for m in fusion_matches}
    colors = _fusion_colors(str(tr.default_catalog_csv_path()))

    def _fuse_with_text(text: str, text_src: str):
        # agents/H1-cpu-path.md: гейт «не подтверждена винодельня» — ТОЛЬКО для
        # источника "ocr" (самый шумный из трёх; offline 87.1% -> 88.7% top-1), НЕ
        # для vlm*-источников (там тот же гейт вреден на offline-прогоне, 95.2% ->
        # 93.5% — см. cv/text_fusion.py докстринг и app/config.py::
        # cv_fusion_ocr_unconfirmed_w).
        unconfirmed_w = (
            settings.cv_fusion_ocr_unconfirmed_w if text_src == "ocr" else settings.cv_fusion_unconfirmed_winery_w
        )
        return text_fusion.fuse(
            cv_scores, text_index, text, family_by_slug=family_by_slug,
            w=settings.cv_fusion_w, gap_floor=settings.cv_fusion_gap_floor, cv_floor=settings.cv_fusion_cv_floor,
            winery_index=winery_index, unconfirmed_winery_w=unconfirmed_w,
            colors=colors, color_penalty=settings.cv_fusion_color_penalty,
        )

    # Тимлид 22.09 (расширение брифа scan-budget, п.9, решение Вячеслава: "модель и
    # локальный путь стартуют одновременно ... ответила — выбираем лучший"): ДВА
    # полных ответа слияния на ОДНИХ И ТЕХ ЖЕ CV-векторах/кандидатной вселенной —
    # `model_result` (`text_source` — уже РАЗРЕШЁННЫЙ источник, после фолбэка
    # vlm*->ocr в `_fusion_text_and_vectors` выше, не сырой
    # `settings.cv_fusion_text_source`: гейт винодельни должен видеть текст, который
    # РЕАЛЬНО участвует в этом запросе) и `local_result` (ВСЕГДА "ocr" — чистый
    # OCR, без модели, независимо от того, что решил `CV_FUSION_TEXT_SOURCE`).
    # Считаются ОБА всегда (не только когда CV_FUSION_CHOOSE!="merge") — офлайн-
    # сравнение ml-lead нужно собирать уже сейчас, пока дефолт "merge" не меняет
    # видимый ответ ни на бит.
    model_result = _fuse_with_text(label_text, text_source)
    local_result = _fuse_with_text(ocr_text, "ocr")
    local_slug = local_result.ranked[0].slug if local_result.ranked else None
    model_slug = model_result.ranked[0].slug if model_result.ranked else None
    answers_agree = (local_slug == model_slug) if (local_slug is not None and model_slug is not None) else None

    chosen_result, chosen_side = _choose_fusion_result(model_result, local_result, settings.cv_fusion_choose)
    logger.info(
        "cv_fusion: local=%s model=%s agree=%s choose=%s chosen=%s",
        local_slug, model_slug, answers_agree, settings.cv_fusion_choose, chosen_side,
    )

    ranked_top = chosen_result.ranked[:top_k]  # v0.4.3/v0.4.11: matches/candidates — top-5, score=final

    chosen_slug = ranked_top[0].slug
    ocr_verified = False
    candidates = [_candidate_item(retriever, c.slug, c.final_score) for c in ranked_top]

    # brief G7 задача 3: верификатор near-dup поверх слияния — ТОЛЬКО за
    # CV_FUSION_VERIFY (дефолт выключен, замерены оба варианта — reports/
    # g7-text-fusion.md). Отбор кандидатов — той же дисциплиной, что путь без
    # слияния (v0.4.8: близость СЫРОГО CV-скора, cv_verify_proximity, cap
    # top-5) — near-dup различение принципиально CV-визуальный феномен (одна
    # этикетка, разный год/категория), поэтому близость мерится по `cv_score`
    # компоненте fused-кандидатов, не по blended `final_score`. Тимлид 22.09
    # (находка при расширении брифа scan-budget): verify() теперь идёт через
    # `_verify_with_budget()` — свой OCR-проход раньше не имел таймаута ВООБЩЕ
    # (см. её докстринг), дедлайн — ОСТАТОК того же `CV_SCAN_BUDGET_S`, что и у
    # OCR текстовой ветки (весь запрос, не отдельный шаг).
    if settings.cv_fusion_verify:
        top1_cv = ranked_top[0].cv_score
        candidate_slugs = _dedupe_preserve_order(
            c.slug for c in ranked_top if (top1_cv - c.cv_score) <= settings.cv_verify_proximity
        )[:5]
        if len(candidate_slugs) > 1:
            verified = _verify_with_budget(
                verifier, image_bytes, _verify_candidates(retriever, candidate_slugs), ocr_text,
                t0 + settings.cv_scan_budget_s,
            )
            if verified is not None and verified in candidate_slugs:
                chosen_slug = verified
                ocr_verified = True

    best_guess_slug = chosen_slug
    # `chosen_result.confident` (cv.text_fusion.fuse()) — ЧИСТЫЙ гейт слияния (brief
    # G7: gap(final)>=CV_FUSION_GAP_FLOOR, или доминирование, И cv_score(top1)>=
    # CV_FUSION_CV_FLOOR) — `fuse()` считается ДО verify() и ничего не знает про
    # OCR-верификацию near-dup. Финальное решение здесь ДОБАВЛЯЕТ тот же обход,
    # что и путь без слияния (v0.4.5, комментарий ниже в non-fusion ветке этого
    # файла): успешная OCR-верификация (`ocr_verified`) обходит ИМЕННО проверку
    # по марже/gap, не по полу — маленький `gap` между near-dup членами ОДНОЙ
    # семьи (например, "Мускатель белый/чёрный" — одна этикетка) и есть определение
    # неоднозначности, ради которой verify() вообще вызывается (CV_FUSION_VERIFY);
    # без этого обхода successfully-verified near-dup ответы всегда проваливали бы
    # gap_floor и уходили в not_in_catalog, обесценивая сам смысл верификатора.
    confident = ranked_top[0].cv_score >= settings.cv_fusion_cv_floor and (
        ocr_verified or chosen_result.gap is None or chosen_result.gap >= settings.cv_fusion_gap_floor
    )

    # ml-lead/тимлид 27.09 (reports/ml-eng-not-a-bottle.md, находка reports/
    # qa-manual-final.md п.3): вето "не бутылка" — по образцу обратного гейта
    # режима «Блюдо» (is_food/is_wine_bottle, app/dish_recognition.py), см.
    # app/config.py::cv_not_a_bottle_veto/cv_not_a_bottle_cv_ceiling. Применяется
    # ТОЛЬКО когда кадр УЖЕ прошёл гейт `confident` (иначе рич-режим и так честно
    # хеджирует, см. фото моря в разборе находки) И модель, реально ответившая
    # в этом запросе, прямо сказала "бутылки/этикетки нет" (`model_no_bottle`,
    # НЕ молчание/таймаут шлюза — см. `_no_bottle_signal()`) И визуальный скор
    # top1 НИЖЕ калиброванного потолка (осторожность с ценой ошибки, бриф п.2:
    # очень уверенное CV-совпадение НИКОГДА не переигрывается словом модели).
    # Эффект — тот же честный исход, что "нет совпадений вовсе" (`best_guess_slug`
    # тоже обнуляется, не только `slug`): flat-путь /v1/eval/predict отдаёт
    # пустой slug ровно так, как уже предусмотрено контрактом для not_in_catalog
    # (contracts/image-scan.md, "flat ВСЕГДА отдаёт лучший доступный slug" — но
    # тут его просто нет, кадр не про вино). Формат ответа не меняется ни одним
    # новым полем.
    not_a_bottle_veto = (
        confident
        and model_no_bottle
        and settings.cv_not_a_bottle_veto
        and ranked_top[0].cv_score < settings.cv_not_a_bottle_cv_ceiling
    )
    if not_a_bottle_veto:
        confident = False
        best_guess_slug = None
        logger.info(
            "cv_fusion: вето 'не бутылка' — модель сообщила bottle_visible=false, "
            "cv_score=%.4f < potolok=%.2f, кадр честно not_in_catalog",
            ranked_top[0].cv_score, settings.cv_not_a_bottle_cv_ceiling,
        )

    identity_rejection = None
    model_fields = getattr(label_text, "fields", {})
    if confident and model_fields:
        from cv.identity import name_conflicts
        entry = getattr(text_index, "catalog", {}).get(chosen_slug)
        conflict = entry is not None and name_conflicts(model_fields, entry, ocr_text)
        if conflict:
            logger.info("cv_identity: name_conflict=True applied=%s", settings.cv_fusion_name_guard)
            if settings.cv_fusion_name_guard:
                confident = False
                identity_rejection = "name_conflict"
                # Flat remains a best-guess contract; rich asks the user to clarify.

    card = build_wine_card(retriever, chosen_slug) if confident else None
    similar: list[dict] = []
    analogs: list[dict] = []
    if not confident:
        similar, analogs = _suggest_similar_and_analogs(
            retriever, (c.slug for c in ranked_top), ranked_top[0].slug,
        )

    return PhotoScanResult(
        best_guess_slug=best_guess_slug,
        slug=chosen_slug if confident else None,
        card=card,
        top1_score=ranked_top[0].cv_score,  # brief G7 п.3: CV-скор top-1, НЕ final
        gap=chosen_result.gap,
        ocr_verified=ocr_verified,
        not_in_catalog=not confident,
        timing_ms=int((time.monotonic() - t0) * 1000),
        similar=similar,
        analogs=analogs,
        matches=[{"slug": c.slug, "score": c.final_score} for c in ranked_top],
        candidates=candidates,
        # Тимлид 22.09 (п.9): text_source/label_text отражают ту сторону, что РЕАЛЬНО
        # выбрана (chosen_side) — "merge" (дефолт) всегда "model", поэтому здесь
        # БИТ В БИТ старое поведение; при "local" — честно "ocr"/ocr_text, а не
        # модельные значения, которые на самом деле не повлияли на ответ.
        identity_rejection=identity_rejection,
        model_label_fields=model_fields,
        text_source=text_source if chosen_side == "model" else "ocr",
        label_text=label_text if chosen_side == "model" else ocr_text,
        local_slug=local_slug,
        model_slug=model_slug,
        answers_agree=answers_agree,
        chosen_answer_side=chosen_side,
    )


# --------------------------------------------------------------------------
# agents/ML-2-shelf-crop.md (22.09): сегментация кадра ЦЕЛОЙ ПОЛКИ на бутылки —
# шаг 0 конвейера, ДО ImageIndex.search()/_run_photo_scan_fusion() (см.
# reports/ml-lead-shelf-crop.md — центральный кроп боевого пути на фото ЦЕЛОЙ
# ПОЛКИ содержит 3-4+ бутылки вместо одной, top-1 падает 93.5%→1/8 на полевых
# фото). Ленивый импорт `cv.shelf_crop` — тот же принцип, что
# `_import_cv_fusion_deps()`/`_import_text_rerank()` выше: apps/api не должен
# требовать packages/cv, пока CV_SHELF_CROP=0 (дефолт).
# --------------------------------------------------------------------------


def _import_shelf_crop():
    try:
        from cv import shelf_crop
    except ImportError as exc:
        raise RuntimeError(
            "CV_SHELF_CROP=1, но пакет packages/cv не установлен в это окружение "
            "(uv sync --extra integration в apps/api). Пока не нужен — оставьте "
            "CV_SHELF_CROP=0 (дефолт)."
        ) from exc
    return shelf_crop


def _pick_best_shelf_crop(
    arr, boxes: list[tuple[int, int, int, int]], image_index: ImageIndex, encode_jpeg,
) -> tuple[int, int, int, int]:
    """agents/ML-2-shelf-crop.md, доп. пункт (риски reports/ml-lead-shelf-crop.md,
    п.2: off-by-one раздела Вороного на пограничном центре, F04/F30) — только
    при `settings.cv_shelf_check_neighbors` и когда `ShelfSegmentation.
    candidate_indices` реально несёт больше одного кандидата (граница между
    колонками почти совпала с X-центром кадра). Пробует КАЖДЫЙ кандидатный кроп
    через `ImageIndex.search()` (дешёвый top-1 ANN — тот же сырой CV-сигнал,
    которому уже доверяет гейт уверенности ниже по конвейеру, не второй проход
    OCR/текстового индекса — дешевле) и оставляет тот, что дал более высокий
    скор. Сбой поиска на конкретном кандидате (крошечный/вырожденный кроп и
    т.п.) — этот кандидат просто не выигрывает, исключение наружу не идёт (весь
    шаг сегментации — усилитель, не обязательный компонент конвейера)."""
    best_box, best_score = boxes[0], float("-inf")
    for candidate in boxes:
        x0, y0, x1, y1 = candidate
        crop = arr[y0:y1, x0:x1]
        score = float("-inf")
        try:
            matches = image_index.search(encode_jpeg(crop), top_k=1)
            if matches:
                score = matches[0].score
        except Exception:  # noqa: BLE001 — деградация: кандидат просто не выигрывает сравнение
            pass
        if score > best_score:
            best_score, best_box = score, candidate
    return best_box


def _apply_shelf_crop(image_bytes: bytes, settings: Settings, image_index: ImageIndex) -> bytes:
    """contracts/image-scan.md НЕ описывает CV_SHELF_CROP (чистый препроцессинг
    входного фото ДО ImageIndex.search()/OCR, формат ответа не меняется — см.
    agents/ML-2-shelf-crop.md). Гейт «это полка»
    (`packages/cv/cv/shelf_crop.py::segment_shelf`) не пройден -> ИСХОДНЫЕ
    БАЙТЫ ПОБИТОВО, без единого перекодирования — регрессия на студийных/
    не-полочных фото структурно невозможна (тот же принцип, что "весь кадр"
    было для конвейера без сегментации).

    Кроп передаётся дальше ПЕРЕКОДИРОВАННЫМ в JPEG (`cv.imageio.encode_jpeg`,
    quality=95), не сырым массивом глубже по стеку: и `ImageIndex.search()`/
    `embed_fusion_query()`, и `LabelVerifier.read_query_text()`/`verify()`
    принимают `bytes` — интерфейс контракта не трогается, а замена входных
    байт ОДИН раз здесь автоматически подхватывается ОБОИМИ путями
    `run_photo_scan` (обычным и `_run_photo_scan_fusion`), не только одним из
    них. Цена — один лишний цикл JPEG-кодирования на полочных фото (гейт
    пройден на меньшинстве кадров, см. reports/ml-eng-ml2.md) — дешевле, чем
    протаскивать ndarray отдельным параметром через все сигнатуры контракта."""
    shelf_crop = _import_shelf_crop()
    from cv.imageio import decode_image, encode_jpeg

    arr = decode_image(image_bytes)  # ValueError на битые байты — как и раньше, просто раньше по времени
    seg = shelf_crop.segment_shelf(
        arr, min_boxes=settings.cv_shelf_min_boxes, min_text_aspect=settings.cv_shelf_min_text_aspect,
    )
    if not seg.is_shelf:
        return image_bytes

    chosen = seg.central_crop
    if settings.cv_shelf_check_neighbors and len(seg.candidate_indices) > 1:
        candidates = [seg.crops[i] for i in seg.candidate_indices]
        chosen = _pick_best_shelf_crop(arr, candidates, image_index, encode_jpeg)

    x0, y0, x1, y1 = chosen
    cropped = arr[y0:y1, x0:x1]
    return encode_jpeg(cropped, quality=95)


def run_photo_scan(
    *,
    image_bytes: bytes,
    image_index: ImageIndex,
    verifier: LabelVerifier,
    retriever: Retriever,
    settings: Settings,
    top_k: int = 5,
    user_box_applied: bool = False,
) -> PhotoScanResult:
    t0 = time.monotonic()

    if settings.cv_shelf_crop and not user_box_applied:
        image_bytes = _apply_shelf_crop(image_bytes, settings, image_index)

    # WineScan performs its own multi-index/text fusion inside search(); the native
    # CV fusion API requires embed_fusion_query/search_fusion, which it does not expose.
    if settings.cv_fusion and settings.image_provider != "winescan":
        return _run_photo_scan_fusion(
            image_bytes=image_bytes, image_index=image_index, verifier=verifier,
            retriever=retriever, settings=settings, top_k=top_k, t0=t0,
        )

    evidence = None
    if settings.image_provider == "winescan" and callable(getattr(image_index, "search_with_details", None)):
        evidence = image_index.search_with_details(image_bytes, top_k=top_k, normalize=not user_box_applied)
        matches = evidence.matches
    else:
        matches = image_index.search(image_bytes, top_k=top_k)

    if not matches:
        return PhotoScanResult(
            best_guess_slug=None, slug=None, card=None, top1_score=None, gap=None,
            ocr_verified=False, not_in_catalog=True,
            timing_ms=int((time.monotonic() - t0) * 1000),
            matches=[], candidates=[],
        )

    # v0.4.12: ОДИН проход OCR по запросу, ДО near-dup routing ниже —
    # переранжирование top-K трогает ВЕСЬ `matches` (не только near-dup
    # ветку), поэтому `ocr_text` читается здесь и переиспользуется near-dup
    # верификатором дальше (см. `verifier.verify(..., ocr_text=ocr_text)`
    # ниже) — второго прохода OCR на этот же запрос нет. Выключенный флаг
    # (дефолт) оставляет `ocr_text=None` -> `verify()` читает OCR сам, бит-
    # в-бит старое поведение (contracts/image-scan.md v0.4.12 п.1-2).
    ocr_text: str | None = evidence.ocr_text if evidence is not None else None
    if settings.cv_text_rerank:
        if ocr_text is None:
            ocr_text = verifier.read_query_text(image_bytes)
        matches = _apply_text_rerank(matches, ocr_text, retriever, settings)

    top = matches[0]
    chosen_slug = top.slug
    ocr_verified = False
    # v0.4.11: candidates считаются от СЫРОГО top-K, независимо от исхода
    # confident/not_in_catalog ниже (см. докстринг _candidate_items) — та же
    # дисциплина, что и у matches (v0.4.3).
    candidates = _candidate_items(retriever, matches)

    # v0.4.8 (контракт, закрытие TODO-2 — диагноз "Мускатель Массандра" q2,
    # reports/g4-family-gap.md): кандидаты верификатора отбираются ПО БЛИЗОСТИ
    # СКОРОВ к top1 (CV_VERIFY_PROXIMITY), НЕЗАВИСИМО от семейной переписи —
    # family-based gap (v0.4.7, G4) с этой волны используется ТОЛЬКО ниже, для
    # маржи not_in_catalog (см. `confident`), к отбору кандидатов отношения
    # больше не имеет.
    #
    # Почему это заменило старую (v0.4.2-v0.4.7) gap-based выборку: линейки с
    # ОДНИМ дизайном этикетки, но РАЗНЫМИ названиями (Портвейн/Мускат/
    # Мускатель у Массандры — q2) перепись семей НЕ склеивает в одну семью —
    # это разные, самостоятельные позиции каталога, не near-dup формально.
    # Family-based gap тогда либо находит "чужого" конкурента буквально на
    # 2-м ранге (раз общей семьи нет вовсе), либо (при полном совпадении
    # семьи) даёт null — в обоих случаях старый `group_floor = top.score -
    # top.gap` схлопывал candidate_slugs до одного top1, хотя ВЕСЬ топ-5 на
    # q2 держался в пределах 0.033 по сырому score (трассировка G4,
    # `packages/cv/scripts/q2_live_check.py`) — верификатор не вызывался
    # вообще, и «явно другая» категория с этикетки никогда не читалась.
    # Проверка на реальном каталоге (`cv/index.py`): ANN-скор — единственный
    # сигнал "визуально одна и та же этикетка", который не зависит от того,
    # успела ли перепись F3 сгруппировать конкретные слаги в семью — отбор
    # кандидатов по нему устойчив ровно к тому классу пробелов переписи,
    # который и породил q2.
    candidate_slugs = _dedupe_preserve_order(
        m.slug for m in matches[:top_k] if (top.score - m.score) <= settings.cv_verify_proximity
    )[:5]  # контракт: cap top-5 явно — не полагаемся на top_k вызывающего кода

    if len(candidate_slugs) > 1:
        # v0.4.4: verify() хочет метаданные каталога (name/vintage), не
        # голые slug'и — см. _verify_candidates(). v0.4.12: ocr_text=None,
        # если text_rerank выключен (дефолт) -> verify() читает OCR сам,
        # ровно как раньше; иначе — переиспользует уже прочитанный текст.
        # Тимлид 22.09 (находка при расширении брифа scan-budget: verify() —
        # общий шаг ДЛЯ ОБОИХ режимов ответа, flat/rich, раньше без таймаута
        # вовсе ни здесь, ни в CV_FUSION-ветке) — тот же `_verify_with_budget()`,
        # тот же бюджет `CV_SCAN_BUDGET_S` от t0, что и в `_run_photo_scan_fusion`.
        verified = _verify_with_budget(
            verifier, image_bytes, _verify_candidates(retriever, candidate_slugs), ocr_text,
            t0 + settings.cv_scan_budget_s,
        )
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
    confident = (evidence is None or evidence.status != "not_found") and top.score >= settings.cv_abs_floor and (
        ocr_verified or top.gap is None or top.gap >= settings.cv_margin_floor
    )

    # v0.4.1: card — ровно тело GET /wines/{id} (включая similar), общий
    # построитель с routers/wines.py (app/rag/cards.py) — не две формы.
    card = build_wine_card(retriever, chosen_slug) if confident else None
    similar: list[dict] = []
    analogs: list[dict] = []
    if not confident:
        similar, analogs = _suggest_similar_and_analogs(retriever, (m.slug for m in matches), top.slug)

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
        candidates=candidates,
    )
