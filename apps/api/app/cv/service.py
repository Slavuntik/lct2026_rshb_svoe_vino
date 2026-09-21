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
    text_source: str | None = None
    label_text: str | None = None


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
def _fusion_winery_index(catalog_csv: str):
    """agents/H1-cpu-path.md: `cv.text_fusion.TextIndexV2` ТОЛЬКО по полю `winery` —
    для гейта «не подтверждена винодельня» (`cv.text_fusion.fuse(winery_index=...)`).
    ОТДЕЛЬНЫЙ индекс/кэш от `_fusion_text_index` выше (тот несёт `FUSION_FIELDS`
    целиком — name+winery+grape+category+sugar, непригоден для recall ИМЕННО поля
    winery) — тот же паттерн ключевания строкой по `catalog_csv`, тот же честный
    выход на отсутствующий CSV (пустой индекс, см. `load_catalog_index`)."""
    _, text_fusion, _ = _import_cv_fusion_deps()
    return text_fusion.load_catalog_index(catalog_csv, fields=("winery",))


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


# Потоки для чтения этикетки параллельно с CV-эмбеддингами (VLM — сетевой запрос,
# PaddleOCR — CPU): общий пул на процесс, не на запрос.
_FUSION_TEXT_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fusion-text")


def _fusion_text_and_vectors(
    image_bytes: bytes, image_index: ImageIndex, verifier: LabelVerifier, settings: Settings,
) -> tuple[str, str, str, tuple[list[float], list[float]]]:
    """(текст для слияния, источник, текст OCR, CV-векторы) — всё параллельно.

    PaddleOCR читается всегда (фолбэк и вход near-dup верификатора). Модели — по
    `CV_FUSION_TEXT_SOURCE`: "vlm" — GPU-сервер (шлюз), "vlm_local" — локальная MLX-модель,
    "vlm_both" — обе, их тексты склеиваются (замер: обе вместе 96.8% против 95.2% у каждой).
    CV-эмбеддинги считаются в этом же потоке, пока текст читается. Модель не ответила к
    общему дедлайну `VISION_LLM_TIMEOUT_S` от начала чтения, ошиблась или вернула пустые
    поля — её вклад пропускается; не ответила ни одна — слияние идёт на тексте OCR.
    Источник в ответе: "vlm", "vlm_local", "vlm_both" (ответили обе) или "ocr"."""
    mode = settings.cv_fusion_text_source
    # Общий дедлайн от начала чтения, а не таймаут на каждую модель: скрипт проверки режет
    # запрос на 10 с, а у шлюза длинный хвост (p95 ~9.8 с на приёмке 21.09) — кто не
    # успел к дедлайну, просто не участвует (в vlm_both локальная 4B отвечает за ~3 с).
    deadline = time.monotonic() + settings.vision_llm_timeout_s
    readers: dict[str, object] = {}
    if mode in ("vlm", "vlm_both") and settings.vision_llm_url and settings.vision_llm_key:
        readers["vlm"] = _FUSION_TEXT_POOL.submit(
            vision_llm.read_label, image_bytes,
            url=settings.vision_llm_url, key=settings.vision_llm_key, model=settings.vision_llm_model,
            timeout_s=settings.vision_llm_timeout_s, image_size=settings.vision_llm_image_size,
        )
    if mode in ("vlm_local", "vlm_both") and settings.vision_llm_local_url:
        readers["vlm_local"] = _FUSION_TEXT_POOL.submit(
            vision_llm.read_label, image_bytes,
            url=settings.vision_llm_local_url, key=None, model=settings.vision_llm_local_model,
            timeout_s=settings.vision_llm_timeout_s, image_size=settings.vision_llm_image_size,
        )
    f_ocr = _FUSION_TEXT_POOL.submit(verifier.read_query_text, image_bytes)
    vectors = image_index.embed_fusion_query(image_bytes)  # ValueError на битых байтах — как раньше
    ocr_text = f_ocr.result()
    got: dict[str, str] = {}
    for name, fut in readers.items():
        try:
            text = fut.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 — не успела к дедлайну/сбой потока: без вклада этой модели
            text = ""
        if text.strip():
            got[name] = text
    if not got:
        return ocr_text, "ocr", ocr_text, vectors
    source = "vlm_both" if len(got) == 2 else next(iter(got))
    return " ".join(got[k] for k in ("vlm", "vlm_local") if k in got), source, ocr_text, vectors


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

    label_text, text_source, ocr_text, vectors = _fusion_text_and_vectors(image_bytes, image_index, verifier, settings)
    text_index = _fusion_text_index(str(tr.default_catalog_csv_path()))
    winery_index = _fusion_winery_index(str(tr.default_catalog_csv_path()))
    family_by_slug = _fusion_family_by_slug(str(cv_families.default_families_path()))

    text_top = text_fusion.text_top_slugs_for_ocr(text_index, label_text, text_fusion.DEFAULT_TEXT_TOP_N)
    # v0.4.13 (brief G7 п.2): CV-скор — max(нормализованный кроп, весь кадр),
    # кандидаты — CV top-K ∪ текстовые extra_slugs (точным фильтрованным
    # запросом Qdrant, не ANN-топ — см. `ImageIndex.search_fusion()`).
    fusion_matches = image_index.search_fusion(
        None, top_k=text_fusion.DEFAULT_ANN_TOP_K, extra_slugs=text_top, vectors=vectors,
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

    # agents/H1-cpu-path.md: гейт «не подтверждена винодельня» — ТОЛЬКО для источника
    # "ocr" (самый шумный из трёх; offline 87.1% -> 88.7% top-1), НЕ для vlm*-источников
    # (там тот же гейт вреден на offline-прогоне, 95.2% -> 93.5% — см. cv/text_fusion.py
    # докстринг и app/config.py::cv_fusion_ocr_unconfirmed_w). `text_source` — уже
    # РАЗРЕШЁННЫЙ источник (после фолбэка vlm*->ocr в `_fusion_text_and_vectors` выше),
    # не сырой `settings.cv_fusion_text_source` — если VLM не ответила и слияние в
    # итоге идёт на тексте OCR, гейт применяется тоже (важно качество текста, который
    # РЕАЛЬНО участвует в этом запросе, не то, что было настроено).
    unconfirmed_winery_w = (
        settings.cv_fusion_ocr_unconfirmed_w if text_source == "ocr" else settings.cv_fusion_unconfirmed_winery_w
    )
    cv_scores = {m.slug: m.score for m in fusion_matches}
    result = text_fusion.fuse(
        cv_scores, text_index, label_text, family_by_slug=family_by_slug,
        w=settings.cv_fusion_w, gap_floor=settings.cv_fusion_gap_floor, cv_floor=settings.cv_fusion_cv_floor,
        winery_index=winery_index, unconfirmed_winery_w=unconfirmed_winery_w,
        colors=_fusion_colors(str(tr.default_catalog_csv_path())), color_penalty=settings.cv_fusion_color_penalty,
    )
    ranked_top = result.ranked[:top_k]  # v0.4.3/v0.4.11: matches/candidates — top-5, score=final

    chosen_slug = ranked_top[0].slug
    ocr_verified = False
    candidates = [_candidate_item(retriever, c.slug, c.final_score) for c in ranked_top]

    # brief G7 задача 3: верификатор near-dup поверх слияния — ТОЛЬКО за
    # CV_FUSION_VERIFY (дефолт выключен, замерены оба варианта — reports/
    # g7-text-fusion.md). Отбор кандидатов — той же дисциплиной, что путь без
    # слияния (v0.4.8: близость СЫРОГО CV-скора, cv_verify_proximity, cap
    # top-5) — near-dup различение принципиально CV-визуальный феномен (одна
    # этикетка, разный год/категория), поэтому близость мерится по `cv_score`
    # компоненте fused-кандидатов, не по blended `final_score`.
    if settings.cv_fusion_verify:
        top1_cv = ranked_top[0].cv_score
        candidate_slugs = _dedupe_preserve_order(
            c.slug for c in ranked_top if (top1_cv - c.cv_score) <= settings.cv_verify_proximity
        )[:5]
        if len(candidate_slugs) > 1:
            verified = verifier.verify(
                image_bytes, _verify_candidates(retriever, candidate_slugs), ocr_text=ocr_text,
            )
            if verified is not None and verified in candidate_slugs:
                chosen_slug = verified
                ocr_verified = True

    best_guess_slug = chosen_slug
    # `result.confident` (cv.text_fusion.fuse()) — ЧИСТЫЙ гейт слияния (brief G7:
    # gap(final)>=CV_FUSION_GAP_FLOOR, или доминирование, И cv_score(top1)>=
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
        ocr_verified or result.gap is None or result.gap >= settings.cv_fusion_gap_floor
    )

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
        gap=result.gap,
        ocr_verified=ocr_verified,
        not_in_catalog=not confident,
        timing_ms=int((time.monotonic() - t0) * 1000),
        similar=similar,
        analogs=analogs,
        matches=[{"slug": c.slug, "score": c.final_score} for c in ranked_top],
        candidates=candidates,
        text_source=text_source,
        label_text=label_text,
    )


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

    if settings.cv_fusion:
        return _run_photo_scan_fusion(
            image_bytes=image_bytes, image_index=image_index, verifier=verifier,
            retriever=retriever, settings=settings, top_k=top_k, t0=t0,
        )

    matches = image_index.search(image_bytes, top_k=top_k)  # ValueError на битом файле — не ловим, пусть роутер решает код ответа

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
    ocr_text: str | None = None
    if settings.cv_text_rerank:
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
        verified = verifier.verify(
            image_bytes, _verify_candidates(retriever, candidate_slugs), ocr_text=ocr_text,
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
    confident = top.score >= settings.cv_abs_floor and (
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
