#!/usr/bin/env python3
"""qa/accuracy_lab.py — офлайн-лаборатория текстового переранжирования и TTA
(agents/G5-accuracy.md, главная задача волны — 50 из 100 баллов кейса за raw top-1 на
приватной выборке). БЕЗ HTTP, напрямую поверх `packages/cv` (`cv.index.ImageIndex`,
`cv.verify.LabelVerifier`, `cv.text_rerank`) — тот же принцип, что `qa/run_cv_index_
baseline.py`/`qa/case_ocr_census.py`: обходим apps/api (не моя зона), меряем ядро.

Честный baseline (qa/scan-eval-runs/case-20260918-honest, индекс case-20260918,
2054 позиции): raw top-1 64,6%, top-5 83,1%, средний gap top1/следующий-чужой ~0,028 —
верный ответ ЧАСТО в top-K, но не первым: узкое место в РАНЖИРОВАНИИ, не в поиске.
Бюджет задержки почти не используется (0,35 с из ≤3 с SLA) — есть место для OCR-сигнала
на КАЖДЫЙ запрос (не только near-dup, как сейчас cv/verify.py), не только на спорные.

## Датасет и сплит

Набор — `case-synth-honest` (2054 синтетических фото, per-slug seed, F4-фикс; тот же,
что дал 64,6%/83,1% baseline), путь по умолчанию — каталог ЭТОЙ сессии в scratchpad
(см. `DEFAULT_PHOTOS_DIR`); если каталога нет (другая сессия) — перегенерировать:
    packages/cv/.venv/bin/python qa/gen_case_synthetic_baseline_photos.py \\
        --out-dir <куда угодно, передать через --photos-dir>
Сплит dev/holdout — ТОТ ЖЕ `qa/scan_eval.py::assign_split` (seed=1337,
holdout_frac=0.2 по умолчанию, стабильный sha256 по photo_id) — импортируется, не
переизобретается, иначе dev агента G5 и dev будущих прогонов F/B разъедутся.

## Кэш (qa/.cache/, в .gitignore) — дорогое считаем ОДИН раз

`photo_cache.jsonl` — по одной JSON-строке на фото: CV top-20 для ТРЁХ вариантов кропа
(`top20_crop` — normalize_query(enabled=True), основной/боевой вариант; `top20_full` —
весь кадр, normalize=False; `top20_center` — наивный центральный кроп без детектора,
для TTA, бриф п.3) + OCR-текст ДВУХ вариантов (`ocr_crop`/`ocr_full`, бриф п.1: "что
читает больше полезного?") + тайминги по стадиям. Идемпотентно: `cache` пропускает уже
готовые photo_id, дописывает недостающее — можно звать много раз подряд (ORCHESTRATION.md,
"длинный шаг — синхронно, батчами"; один прогон на ~2054 фото не укладывается в один
bash-вызов ≤10 мин при OCR ~300-450 мс/вариант — см. reports/g5-accuracy.md, "Задержка").
Обрыв процесса посреди записи строки чинится сам (`_repair_truncate_partial_line`) —
следующий вызов `cache` дочитает "хвост" как недостающий, без ручного вмешательства.

## Подкоманды

    cache    — построить/дополнить кэш (--limit ограничивает ЧИСЛО НОВЫХ фото за вызов,
               не общий размер — для батчевания по времени).
    tune     — свип K∈{5,10,20} x w x {ocr_crop,ocr_full} на DEV. Пишет tune_results.json.
    tta      — TTA: слияние max/mean по {crop, full, center} на DEV, с текстом поверх
               лучшей (K,w) из tune. Пишет tta_results.json.
    holdout  — ФИНАЛЬНЫЕ цифры (база / rerank / +TTA) на HOLDOUT, лучшая конфигурация
               с dev. Прогонять один раз, эти цифры идут в отчёт.
    ocr_yield — "выход" OCR (доля фото с ≥1 различающим токеном, средняя длина текста):
               синтетика (из кэша) vs реальные фото (3 контрольных + 7 полочных + стенд).
               НЕ трогает ImageIndex/Qdrant — можно звать параллельно с `cache`.
    sanity   — 3 контрольных фото case-data/eval/queries + 7 полочных field-shots —
               без подгонки, отдельной строкой отчёта.
    latency  — задержка по стадиям на этой машине: encoder.benchmark (БЕЗ кэша эмбеддинга
               — иначе меряем dict-lookup, encoder.py же честно об этом предупреждает) +
               ANN-only + OCR (оба варианта, engine прогрет) + text_rerank compute.

Запускать ТОЛЬКО через venv пакета cv (torch/cv2/qdrant-client/paddleocr):
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    packages/cv/.venv/bin/python qa/accuracy_lab.py cache --limit 400
    ... (повторять, пока не покроет весь датасет) ...
    packages/cv/.venv/bin/python qa/accuracy_lab.py tune
    packages/cv/.venv/bin/python qa/accuracy_lab.py tta
    packages/cv/.venv/bin/python qa/accuracy_lab.py holdout
    packages/cv/.venv/bin/python qa/accuracy_lab.py sanity
    packages/cv/.venv/bin/python qa/accuracy_lab.py latency

На Mac (mps) флаги PADDLE_* не нужны (см. брифа G5 — oneDNN работает); отключение —
только на ams3 (CPU). Embedded-qdrant однопроцессный — перед прогоном убедиться, что
никто не держит лок (`lsof +D packages/cv/data/qdrant`), `index.store.close()` в конце
каждого подпроцесса (см. `finally` в `cmd_cache`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_CV_PKG_DIR = _REPO_ROOT / "packages" / "cv"

sys.path.insert(0, str(_QA_DIR))
sys.path.insert(0, str(_CV_PKG_DIR))
import scan_eval as se  # noqa: E402 — путь добавлен строкой выше; та же схема, что run_cv_index_baseline.py

# Датасет ЭТОЙ сессии (agents/G5-accuracy.md) — см. докстринг модуля про перегенерацию,
# если каталог не существует (другая сессия/машина).
DEFAULT_PHOTOS_DIR = Path(
    "/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/"
    "3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-honest"
)
DEFAULT_CACHE_DIR = _QA_DIR / ".cache"
DEFAULT_CACHE_FILE = "photo_cache.jsonl"

CASE_DATA_DIR_DEFAULT = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")

TOP_K_CACHE = 20  # брифа: "CV top-20" — cache во всех вариантах кропа на эту глубину
K_GRID = [5, 10, 20]
# Расширено вверх по замечанию оркестратора (21.09, разбор промежуточного tune_results.json):
# на ПОЛНОМ dev (где ~2/3 OCR пуст, см. cmd_ocr_yield) свип "садился" на w~0.01 — почти
# выключенный текст, потому что пустые записи (text_score=0 для всех) разбавляют сигнал
# ровно константой независимо от w, а на НЕПУСТОМ подмножестве (единственном месте, где w
# вообще на что-то влияет) малые w могут быть недостаточны, чтобы 1-2 совпавших редких
# токена перевесили родные +-0.01..0.05 разрывы CV-скора (mean_gap ~0.028, reports/
# f3-synthetic-baseline.md). Большие w оценивают именно ЭТУ гипотезу — см. cmd_tune,
# сравнение full-set vs informative-subset.
W_GRID = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0]
OCR_FIELDS = ["ocr_crop", "ocr_full"]


# ========================================================================================
# Общее: загрузка датасета, кэш (resume-safe JSONL)
# ========================================================================================


def load_items(photos_dir: Path) -> list[se.EvalItem]:
    items, warnings = se.load_eval_set(photos_dir)
    for w in warnings:
        print(f"[accuracy_lab] WARN {w}", file=sys.stderr)
    if not items:
        raise SystemExit(f"[accuracy_lab] пустой датасет в {photos_dir} — проверь --photos-dir")
    return items


def _repair_truncate_partial_line(cache_path: Path) -> None:
    """Обрыв процесса (таймаут bash-вызова, kill) посреди `fh.write()` может оставить
    последнюю строку кэша без завершающего `\\n` — следующий append дописал бы поверх
    неё и породил бы один битый JSON на две записи. Обрезаем такой "хвост" ДО append —
    следующий прогон честно перепосчитает этот один photo_id (идемпотентно, не потеря)."""
    if not cache_path.is_file():
        return
    data = cache_path.read_bytes()
    if not data or data.endswith(b"\n"):
        return
    last_nl = data.rfind(b"\n")
    fixed = data[: last_nl + 1] if last_nl >= 0 else b""
    if len(fixed) != len(data):
        cache_path.write_bytes(fixed)
        print("[accuracy_lab] обрезан незавершённый хвост кэша (обрыв предыдущего прогона)", file=sys.stderr)


def _read_cache(cache_path: Path) -> dict[str, dict]:
    """photo_id -> запись. Записи с `error` (битое фото на прошлом прогоне) исключены —
    вызывающий код видит только полные записи; сколько исключено — печатается отдельно."""
    if not cache_path.is_file():
        return {}
    out: dict[str, dict] = {}
    n_errors = 0
    with cache_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # см. _repair_truncate_partial_line — не должно случаться, но не роняем чтение
            pid = rec.get("photo_id")
            if not pid:
                continue
            if rec.get("error"):
                n_errors += 1
                continue
            out[pid] = rec
    if n_errors:
        print(f"[accuracy_lab] в кэше {n_errors} записей с ошибкой (пропущены)", file=sys.stderr)
    return out


# ========================================================================================
# cache — построение/докачка кэша CV top-20 (3 варианта кропа) + OCR (2 варианта)
# ========================================================================================


def _center_crop_array(arr, frac: float = 0.6, out_size: int = 448):
    """Наивный центральный кроп — TTA-вариант "центр" (бриф G5 п.3), сознательно БЕЗ
    детектора этикетки и БЕЗ фотометрии (в отличие от normalize_query(enabled=True)):
    единственная переменная между тремя TTA-вариантами — ГЕОМЕТРИЯ кропа, не остальной
    препроцессинг (letterbox — тот же `imageio.letterbox_resize`, что normalize_query
    (enabled=False) использует для "весь кадр" — сравнение чистое)."""
    h, w = arr.shape[:2]
    side = int(min(h, w) * frac)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    cropped = arr[y0 : y0 + side, x0 : x0 + side]
    from cv import imageio

    return imageio.letterbox_resize(cropped, out_size)


def _collapse_matches(raw: list[tuple[str, float, dict]]) -> list[tuple[str, float]]:
    """Схлопывание ANN-хитов по ракурсам в позиции (лучший скор на slug), по убыванию.
    Дублирует ~10 строк `cv.index.ImageIndex.search()` НАМЕРЕННО, не импортом: тот метод
    принимает готовые image-байты и сам гоняет normalize_query внутри — у TTA-варианта
    "центр" уже есть СВОЙ подготовленный массив (см. `_center_crop_array`), а публичный
    контракт `search(image: bytes, ...)` не даёt точки входа "вот массив, просто
    заэмбедь и поищи" (и не должен — расширять контракт ради лабораторного скрипта
    избыточно, см. ORCHESTRATION.md п.1). `gap` здесь не нужен — top-1/top-5 без гейта."""
    best: dict[str, float] = {}
    for _id, score, payload in raw:
        slug = payload.get("slug")
        if slug is None:
            continue
        if slug not in best or score > best[slug]:
            best[slug] = score
    return sorted(best.items(), key=lambda kv: kv[1], reverse=True)


def _search_array(index, arr, top_k: int) -> list[tuple[str, float]]:
    from cv import config

    vector = index.encoder.encode(arr)
    overfetch = max(top_k * config.SEARCH_OVERFETCH, top_k + 10)
    raw = index.store.search(index.collection, vector, top_k=overfetch)
    return _collapse_matches(raw)[:top_k]


def cmd_cache(args: argparse.Namespace) -> int:
    from cv import imageio
    from cv.index import ImageIndex
    from cv.normalize import normalize_query
    from cv.verify import LabelVerifier

    items = load_items(args.photos_dir)
    cache_dir: Path = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / DEFAULT_CACHE_FILE

    _repair_truncate_partial_line(cache_path)
    done = set(_read_cache(cache_path))
    print(f"[cache] {len(done)}/{len(items)} уже в кэше ({cache_path})", file=sys.stderr)

    todo = [it for it in items if it.photo_id not in done]
    if args.limit is not None:
        todo = todo[: args.limit]
    if not todo:
        print("[cache] нечего делать — либо кэш полон, либо --limit 0", file=sys.stderr)
        return 0
    print(f"[cache] в очереди на этот вызов: {len(todo)}", file=sys.stderr)

    index = ImageIndex()
    print(f"[cache] index_version={index.index_version}", file=sys.stderr)
    verifier = LabelVerifier()

    t_start = time.perf_counter()
    n_done = 0
    try:
        with cache_path.open("a", encoding="utf-8") as fh:
            for i, it in enumerate(todo, start=1):
                try:
                    data = it.path.read_bytes()
                    t0 = time.perf_counter()
                    arr = imageio.decode_image(data)
                    t1 = time.perf_counter()
                    normed = normalize_query(arr, enabled=True)
                    t2 = time.perf_counter()
                    top20_crop = index.search(data, top_k=TOP_K_CACHE, normalize=True)
                    t3 = time.perf_counter()
                    ocr_crop = verifier.read_text(normed)
                    t4 = time.perf_counter()
                    whole = normalize_query(arr, enabled=False)
                    t5 = time.perf_counter()
                    top20_full = index.search(data, top_k=TOP_K_CACHE, normalize=False)
                    t6 = time.perf_counter()
                    ocr_full = verifier.read_text(whole)
                    t7 = time.perf_counter()
                    center = _center_crop_array(arr)
                    top20_center = _search_array(index, center, TOP_K_CACHE)
                    t8 = time.perf_counter()
                except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
                    print(f"[cache] WARN {it.photo_id}: {exc}", file=sys.stderr)
                    fh.write(json.dumps({"photo_id": it.photo_id, "true_slug": it.true_slug, "error": str(exc)}, ensure_ascii=False) + "\n")
                    fh.flush()
                    continue

                record = {
                    "photo_id": it.photo_id,
                    "true_slug": it.true_slug,
                    "top20_crop": [{"slug": m.slug, "score": m.score, "gap": m.gap, "view": m.view} for m in top20_crop],
                    # top20_full — та же публичная ImageIndex.search(normalize=False) -> list[Match],
                    # top20_center — наш локальный _search_array() -> list[tuple[slug, score]] (см. выше).
                    "top20_full": [{"slug": m.slug, "score": m.score, "gap": m.gap, "view": m.view} for m in top20_full],
                    "top20_center": [{"slug": s, "score": sc} for s, sc in top20_center],
                    "ocr_crop": ocr_crop,
                    "ocr_full": ocr_full,
                    "timings_ms": {
                        "decode": round((t1 - t0) * 1000, 3),
                        "normalize_crop": round((t2 - t1) * 1000, 3),
                        "search_crop": round((t3 - t2) * 1000, 3),
                        "ocr_crop": round((t4 - t3) * 1000, 3),
                        "normalize_full": round((t5 - t4) * 1000, 3),
                        "search_full": round((t6 - t5) * 1000, 3),
                        "ocr_full": round((t7 - t6) * 1000, 3),
                        "center_crop_and_search": round((t8 - t7) * 1000, 3),
                    },
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                n_done += 1

                if i % 25 == 0 or i == len(todo):
                    elapsed = time.perf_counter() - t_start
                    rate = i / elapsed if elapsed > 0 else 0.0
                    eta = (len(todo) - i) / rate if rate > 0 else float("inf")
                    print(f"[cache] {i}/{len(todo)} за {elapsed:.0f}с ({rate:.2f}/с, ETA {eta:.0f}с)", file=sys.stderr)
    finally:
        index.store.close()

    print(f"[cache] готово: +{n_done} новых записей в {cache_path}", file=sys.stderr)
    return 0


# ========================================================================================
# tune — свип K x w x {ocr_crop, ocr_full} на DEV
# ========================================================================================


def _catalog_and_idf(args: argparse.Namespace):
    from cv.text_rerank import build_idf, load_catalog_text

    catalog = load_catalog_text(args.catalog_csv)
    idf = build_idf(catalog)
    print(f"[tune] каталог: {len(catalog)} слагов, словарь IDF: {len(idf)} токенов", file=sys.stderr)
    return catalog, idf


def _split_records(records: dict[str, dict], seed: int, holdout_frac: float) -> tuple[list[dict], list[dict]]:
    dev, holdout = [], []
    for pid, rec in records.items():
        split = se.assign_split(pid, seed, holdout_frac)
        (dev if split == "dev" else holdout).append(rec)
    return dev, holdout


def _eval_config(
    records: list[dict], ocr_field: str, k: int, w: float, catalog, idf,
    top20_key: str = "top20_crop", min_token_idf: float | None = None,
) -> dict:
    from cv.text_rerank import rerank_top_k

    n = len(records)
    hits1 = hits5 = 0
    for rec in records:
        ranked = [(m["slug"], m["score"]) for m in rec[top20_key]]
        reranked = rerank_top_k(ranked, rec.get(ocr_field, "") or "", catalog, idf, k=k, w=w, min_token_idf=min_token_idf)
        top5 = [s for s, _ in reranked[:5]]
        if top5 and top5[0] == rec["true_slug"]:
            hits1 += 1
        if rec["true_slug"] in top5:
            hits5 += 1
    return {"n": n, "top1": hits1 / n if n else 0.0, "top5": hits5 / n if n else 0.0}


def cmd_tune(args: argparse.Namespace) -> int:
    from cv.text_rerank import distinctive_idf_threshold, has_distinctive_token

    records = _read_cache(args.cache_dir / DEFAULT_CACHE_FILE)
    if not records:
        raise SystemExit("[tune] кэш пуст — сначала `cache` (см. докстринг модуля)")
    dev, holdout = _split_records(records, args.seed, args.holdout_frac)
    print(f"[tune] кэш: {len(records)} записей -> dev={len(dev)} holdout={len(holdout)}", file=sys.stderr)

    catalog, idf = _catalog_and_idf(args)
    threshold = distinctive_idf_threshold(idf)
    print(f"[tune] порог 'различающего' токена (медиана IDF): {threshold:.3f}", file=sys.stderr)

    base = _eval_config(dev, "ocr_crop", k=5, w=0.0, catalog=catalog, idf=idf)
    print(f"[tune] БАЗА (без rerank, весь dev): top1={base['top1']:.4f} top5={base['top5']:.4f} (n={base['n']})", file=sys.stderr)

    # Поправка оркестратора (21.09): на ПОЛНОМ dev пустой/невыразительный OCR (см.
    # cmd_ocr_yield — на синтетике это большинство фото) разбавляет свип константой,
    # не позволяя увидеть, работает ли текстовый скор ТАМ, где ему есть с чем работать.
    # Свип гоняем на ТРЁХ срезах: full БЕЗ гейта (документирует РИСК — найдено 21.09:
    # ~30% синтетики дают непустой, но бессодержательный OCR, который без гейта на
    # full-dev уже РЕГРЕССИРУЕТ на w=0.01), full С ГЕЙТОМ (`min_token_idf=threshold`,
    # безопасная конфигурация — идёт в holdout/отчёт) и informative (только фото, где
    # OCR дал хотя бы один различающий токен — здесь виден истинный эффект
    # переранжирования, гейт на этом срезе тождественно ничего не меняет по построению).
    informative_dev = {
        ocr_field: [r for r in dev if has_distinctive_token(r.get(ocr_field, "") or "", idf, threshold)]
        for ocr_field in OCR_FIELDS
    }
    for ocr_field in OCR_FIELDS:
        n_inf = len(informative_dev[ocr_field])
        print(f"[tune] {ocr_field}: информативных на dev {n_inf}/{len(dev)} ({n_inf / len(dev):.1%})", file=sys.stderr)

    sweep_ungated: list[dict] = []
    sweep_gated: list[dict] = []
    sweep_informative: list[dict] = []
    for ocr_field in OCR_FIELDS:
        inf_records = informative_dev[ocr_field]
        for k in K_GRID:
            for w in W_GRID:
                res_ungated = _eval_config(dev, ocr_field, k, w, catalog, idf)
                sweep_ungated.append({"ocr_field": ocr_field, "k": k, "w": w, **res_ungated})
                res_gated = _eval_config(dev, ocr_field, k, w, catalog, idf, min_token_idf=threshold)
                sweep_gated.append({"ocr_field": ocr_field, "k": k, "w": w, **res_gated})
                if inf_records:
                    res_inf = _eval_config(inf_records, ocr_field, k, w, catalog, idf)
                    sweep_informative.append({"ocr_field": ocr_field, "k": k, "w": w, **res_inf})

    # "Лучшая" на ПОЛНОМ dev С ГЕЙТОМ — цифра, которую реально увидит holdout/отчёт.
    # Безопасность теперь распространяется на ВЕСЬ непустой-но-невыразительный OCR, не
    # только на буквально пустую строку (см. test_rerank_top_k_nonempty_uninformative_
    # ocr_preserves_cv_order_with_gate в packages/cv/tests/test_text_rerank.py).
    best_gated = max(sweep_gated, key=lambda r: (r["top1"], r["top5"]))
    best_ungated = max(sweep_ungated, key=lambda r: (r["top1"], r["top5"]))  # только для сравнения в логе/отчёте

    # "Лучшая" на informative-подмножестве — где сигнал вообще МОГ бы проявиться. ВАЖНО:
    # сравниваем по ПРИРОСТУ над w=0 БАЗОЙ ТОГО ЖЕ ocr_field, не по сырому top1 — иначе
    # выбор нечестно тянет к полю с МЕНЬШИМ (и потому более лёгким/шумным) подмножеством
    # только потому, что ему повезло с базовой точностью (найдено на прогоне 21.09:
    # ocr_full — n=14, top1 у w=0 85.7% — выигрывал у ocr_crop, n=52, w=0 80.8%, при
    # том что ЛЮБОЙ w>0 только ПОРТИЛ ocr_full и явно УЛУЧШАЛ ocr_crop на малых w —
    # ocr_full просто "лёгкое" подмножество, не лучший источник сигнала для rerank).
    # Тай-брейк: больше n (меньше шума) -> меньше w (минимальное вмешательство).
    base_informative = {
        ocr_field: _eval_config(informative_dev[ocr_field], ocr_field, k=5, w=0.0, catalog=catalog, idf=idf)
        for ocr_field in OCR_FIELDS if informative_dev[ocr_field]
    }
    best_informative = None
    if sweep_informative:
        def _lift_key(r: dict) -> tuple:
            base_top1 = base_informative[r["ocr_field"]]["top1"]
            return (round(r["top1"] - base_top1, 6), r["n"], -r["w"])

        best_informative = max(sweep_informative, key=_lift_key)

    print(f"[tune] ЛУЧШАЯ на full dev С ГЕЙТОМ: {best_gated}", file=sys.stderr)
    print(f"[tune] (для сравнения) лучшая БЕЗ гейта: {best_ungated}", file=sys.stderr)
    if best_informative:
        base_inf_same_field = base_informative.get(best_informative["ocr_field"])
        lift = best_informative["top1"] - base_inf_same_field["top1"]
        print(
            f"[tune] ЛУЧШАЯ на informative-подмножестве dev (по приросту над w=0): {best_informative} "
            f"(база w=0 там же: {base_inf_same_field}, прирост {lift:+.4f})",
            file=sys.stderr,
        )

    # Демонстрация РИСКА без гейта (для отчёта): та же (K,w,ocr), что рекомендована по
    # informative-подмножеству, оценённая на ПОЛНОМ dev С гейтом и БЕЗ.
    recommended = best_informative if (best_informative and best_informative["n"] >= 5) else best_gated
    rk, rw, rocr = recommended["k"], recommended["w"], recommended["ocr_field"]
    full_at_recommended_gated = _eval_config(dev, rocr, rk, rw, catalog, idf, min_token_idf=threshold)
    full_at_recommended_ungated = _eval_config(dev, rocr, rk, rw, catalog, idf)
    print(
        f"[tune] рекомендуемая (K={rk},w={rw},ocr={rocr}) на ПОЛНОМ dev: "
        f"с гейтом top1={full_at_recommended_gated['top1']:.4f} (база {base['top1']:.4f}), "
        f"БЕЗ гейта top1={full_at_recommended_ungated['top1']:.4f}",
        file=sys.stderr,
    )

    for row in sorted(sweep_gated, key=lambda r: (-r["top1"], -r["top5"]))[:8]:
        print(f"  [full+gate]   ocr={row['ocr_field']:9s} K={row['k']:2d} w={row['w']:<5} top1={row['top1']:.4f} top5={row['top5']:.4f}", file=sys.stderr)
    for row in sorted(sweep_informative, key=lambda r: (-r["top1"], -r["top5"]))[:8]:
        print(f"  [informative] ocr={row['ocr_field']:9s} K={row['k']:2d} w={row['w']:<5} top1={row['top1']:.4f} top5={row['top5']:.4f} (n={row['n']})", file=sys.stderr)

    out = {
        "n_dev": len(dev),
        "n_holdout": len(holdout),
        "distinctive_idf_threshold": threshold,
        "base_dev": base,
        "base_informative_dev": base_informative,
        "n_informative_dev": {k: len(v) for k, v in informative_dev.items()},
        "sweep_gated": sweep_gated,
        "sweep_ungated": sweep_ungated,
        "sweep_informative": sweep_informative,
        "best": best_gated,  # обратная совместимость имени поля (cmd_tta/cmd_holdout/cmd_sanity читают "best")
        "best_gated": best_gated,
        "best_ungated": best_ungated,
        "best_informative": best_informative,
        "recommended": recommended,
        "recommended_full_dev_gated": full_at_recommended_gated,
        "recommended_full_dev_ungated": full_at_recommended_ungated,
    }
    out_path = args.cache_dir / "tune_results.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[tune] записано: {out_path}", file=sys.stderr)
    return 0


def _pick_recommended_config(tune: dict, min_n: int = 5) -> dict:
    """РЕКОМЕНДУЕМАЯ (K,w,ocr) — поправка оркестратора (21.09): full-set оптимум почти
    всегда сваливается в w≈0, потому что на пустом/невыразительном OCR (подавляющее
    большинство синтетики, см. cmd_ocr_yield) text_score≡0 для ЛЮБОГО w — full-set
    метрика попросту нечувствительна к w за пределами шума. Сигнал живёт ТОЛЬКО на
    informative-подмножестве (где OCR дал различающий токен) — оттуда и берём w, если
    подмножество не совсем крошечное (`min_n`, иначе один-два фото решают всё).

    Предпочитает готовое поле `tune["recommended"]` (записано `cmd_tune` — та же логика,
    там же и посчитано); пересчёт ниже — фолбэк для tune_results.json более старого
    формата (до появления гейта `min_token_idf`, 21.09)."""
    if "recommended" in tune:
        return tune["recommended"]
    best_inf = tune.get("best_informative")
    if best_inf and best_inf.get("n", 0) >= min_n:
        return best_inf
    return tune.get("best_gated") or tune["best"]


# ========================================================================================
# tta — слияние max/mean по {crop, full, center} на DEV, поверх лучшей (K,w) с tune
# ========================================================================================


def _merge_scores(*lists: list[dict], mode: str, top_k: int) -> list[tuple[str, float]]:
    """`lists` — top20_* записи кэша (list[{"slug","score",...}]) по каждому TTA-кропу.
    max/mean считается ТОЛЬКО по кропам, где slug реально встретился в их top-20 —
    отсутствие в конкретном ракурсе не наказывается нулём (кроп мог просто не
    попасть в overfetch top-20 этого ракурса, это не сигнал "непохоже")."""
    from collections import defaultdict

    by_slug: dict[str, list[float]] = defaultdict(list)
    for lst in lists:
        for m in lst:
            by_slug[m["slug"]].append(m["score"])
    if mode == "max":
        merged = [(slug, max(scores)) for slug, scores in by_slug.items()]
    elif mode == "mean":
        merged = [(slug, sum(scores) / len(scores)) for slug, scores in by_slug.items()]
    else:
        raise ValueError(f"неизвестный merge mode: {mode!r}")
    merged.sort(key=lambda kv: kv[1], reverse=True)
    return merged[:top_k]


_TTA_COMBOS = {
    "crop+full": ("top20_crop", "top20_full"),
    "crop+center": ("top20_crop", "top20_center"),
    "crop+full+center": ("top20_crop", "top20_full", "top20_center"),
}


def cmd_tta(args: argparse.Namespace) -> int:
    from cv.text_rerank import distinctive_idf_threshold, rerank_top_k

    records = _read_cache(args.cache_dir / DEFAULT_CACHE_FILE)
    if not records:
        raise SystemExit("[tta] кэш пуст — сначала `cache`")
    dev, holdout = _split_records(records, args.seed, args.holdout_frac)
    catalog, idf = _catalog_and_idf(args)
    threshold = distinctive_idf_threshold(idf)

    tune_path = args.cache_dir / "tune_results.json"
    if not tune_path.is_file():
        raise SystemExit("[tta] нет tune_results.json — сначала `tune`")
    tune = json.loads(tune_path.read_text(encoding="utf-8"))
    best = _pick_recommended_config(tune)
    k, w, ocr_field = best["k"], best["w"], best["ocr_field"]
    print(f"[tta] рекомендованная (K,w,ocr) с dev (informative-подмножество, если хватило n): K={k} w={w} ocr={ocr_field}", file=sys.stderr)

    results: list[dict] = []
    for combo_name, keys in _TTA_COMBOS.items():
        for mode in ("max", "mean"):
            n = len(dev)
            hits1_cv = hits5_cv = 0  # TTA-мёрдж CV-скора БЕЗ текстового rerank
            hits1_rr = hits5_rr = 0  # TTA-мёрдж + текстовый rerank поверх (гейт включён — прод-конфигурация)
            for rec in dev:
                lists = [rec[key] for key in keys]
                merged = _merge_scores(*lists, mode=mode, top_k=TOP_K_CACHE)
                top5_cv = [s for s, _ in merged[:5]]
                if top5_cv and top5_cv[0] == rec["true_slug"]:
                    hits1_cv += 1
                if rec["true_slug"] in top5_cv:
                    hits5_cv += 1
                reranked = rerank_top_k(merged, rec.get(ocr_field, "") or "", catalog, idf, k=k, w=w, min_token_idf=threshold)
                top5_rr = [s for s, _ in reranked[:5]]
                if top5_rr and top5_rr[0] == rec["true_slug"]:
                    hits1_rr += 1
                if rec["true_slug"] in top5_rr:
                    hits5_rr += 1
            results.append({
                "combo": combo_name, "mode": mode, "n": n,
                "tta_only_top1": hits1_cv / n, "tta_only_top5": hits5_cv / n,
                "tta_plus_rerank_top1": hits1_rr / n, "tta_plus_rerank_top5": hits5_rr / n,
            })

    for row in sorted(results, key=lambda r: -r["tta_plus_rerank_top1"]):
        print(f"  {row['combo']:18s} {row['mode']:4s} tta_only_top1={row['tta_only_top1']:.4f} "
              f"+rerank_top1={row['tta_plus_rerank_top1']:.4f} +rerank_top5={row['tta_plus_rerank_top5']:.4f}", file=sys.stderr)

    best_tta = max(results, key=lambda r: (r["tta_plus_rerank_top1"], r["tta_plus_rerank_top5"]))
    # база для сравнения: rerank БЕЗ TTA (только top20_crop) с теми же (K,w,ocr), гейт включён
    base_rerank = _eval_config(dev, ocr_field, k, w, catalog, idf, top20_key="top20_crop", min_token_idf=threshold)
    print(f"[tta] rerank без TTA (dev, для сравнения): top1={base_rerank['top1']:.4f} top5={base_rerank['top5']:.4f}", file=sys.stderr)
    print(f"[tta] ЛУЧШИЙ TTA-вариант (dev): {best_tta}", file=sys.stderr)

    out = {"best_kw_from_tune": best, "rerank_no_tta_dev": base_rerank, "sweep": results, "best_tta": best_tta}
    out_path = args.cache_dir / "tta_results.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[tta] записано: {out_path}", file=sys.stderr)
    return 0


# ========================================================================================
# holdout — ФИНАЛЬНЫЕ цифры (база / rerank / +TTA), один раз, идут в отчёт
# ========================================================================================


def cmd_holdout(args: argparse.Namespace) -> int:
    from cv.text_rerank import distinctive_idf_threshold, has_distinctive_token, rerank_top_k

    records = _read_cache(args.cache_dir / DEFAULT_CACHE_FILE)
    if not records:
        raise SystemExit("[holdout] кэш пуст — сначала `cache`")
    dev, holdout = _split_records(records, args.seed, args.holdout_frac)
    print(f"[holdout] n_dev={len(dev)} n_holdout={len(holdout)}", file=sys.stderr)
    catalog, idf = _catalog_and_idf(args)
    threshold = distinctive_idf_threshold(idf)

    tune_path = args.cache_dir / "tune_results.json"
    tune = json.loads(tune_path.read_text(encoding="utf-8")) if tune_path.is_file() else None
    tta_path = args.cache_dir / "tta_results.json"
    tta = json.loads(tta_path.read_text(encoding="utf-8")) if tta_path.is_file() else None

    # 1) база: чистый порядок CV (top20_crop), без rerank
    base = _eval_config(holdout, "ocr_crop", k=5, w=0.0, catalog=catalog, idf=idf)

    # 2) переранжирование — рекомендованная (K,w,ocr) с dev (tune), гейт ВКЛЮЧЁН
    # (прод-конфигурация — безопасен даже на непустом-но-невыразительном OCR, см.
    # cv.text_rerank.has_distinctive_token), проверено на holdout
    if tune is not None:
        b = _pick_recommended_config(tune)
        rerank = _eval_config(holdout, b["ocr_field"], b["k"], b["w"], catalog, idf, min_token_idf=threshold)
        rerank_cfg = {"k": b["k"], "w": b["w"], "ocr_field": b["ocr_field"], "min_token_idf": threshold}
        rerank_ungated = _eval_config(holdout, b["ocr_field"], b["k"], b["w"], catalog, idf)  # для сравнения — риск без гейта
    else:
        rerank, rerank_cfg, rerank_ungated = None, None, None

    # 2b) ТОТ ЖЕ rerank, но на informative-подмножестве HOLDOUT (поправка оркестратора,
    # 21.09): полный holdout честен как ИТОГОВАЯ цифра (идёт в отчёт как есть), но
    # разбавлен фото с пустым/невыразительным OCR (см. cmd_ocr_yield) — здесь видно,
    # реплицируется ли эффект переранжирования вне dev, КОГДА OCR действительно
    # что-то прочитал (на этом срезе гейт тождественно ничего не меняет по построению).
    rerank_informative = None
    rerank_informative_base = None
    if tune is not None:
        b = _pick_recommended_config(tune)
        inf_holdout = [r for r in holdout if has_distinctive_token(r.get(b["ocr_field"], "") or "", idf, threshold)]
        if inf_holdout:
            rerank_informative = _eval_config(inf_holdout, b["ocr_field"], b["k"], b["w"], catalog, idf)
            rerank_informative_base = _eval_config(inf_holdout, b["ocr_field"], k=5, w=0.0, catalog=catalog, idf=idf)

    # 3) +TTA — лучшая связка с dev (tta), проверено на holdout, гейт включён
    tta_holdout = None
    tta_cfg = None
    if tune is not None and tta is not None:
        b = _pick_recommended_config(tune)
        bt = tta["best_tta"]
        keys = _TTA_COMBOS[bt["combo"]]

        n = len(holdout)
        hits1 = hits5 = 0
        for rec in holdout:
            lists = [rec[key] for key in keys]
            merged = _merge_scores(*lists, mode=bt["mode"], top_k=TOP_K_CACHE)
            reranked = rerank_top_k(merged, rec.get(b["ocr_field"], "") or "", catalog, idf, k=b["k"], w=b["w"], min_token_idf=threshold)
            top5 = [s for s, _ in reranked[:5]]
            if top5 and top5[0] == rec["true_slug"]:
                hits1 += 1
            if rec["true_slug"] in top5:
                hits5 += 1
        tta_holdout = {"n": n, "top1": hits1 / n, "top5": hits5 / n}
        tta_cfg = {"combo": bt["combo"], "mode": bt["mode"], **rerank_cfg}

    print(f"[holdout] БАЗА          top1={base['top1']:.4f} top5={base['top5']:.4f}", file=sys.stderr)
    if rerank:
        print(f"[holdout] +RERANK с гейтом ({rerank_cfg}) top1={rerank['top1']:.4f} top5={rerank['top5']:.4f}", file=sys.stderr)
        print(f"[holdout] +RERANK БЕЗ гейта (риск, для сравнения) top1={rerank_ungated['top1']:.4f} top5={rerank_ungated['top5']:.4f}", file=sys.stderr)
    if rerank_informative:
        print(
            f"[holdout] +RERANK на informative-подмножестве (n={rerank_informative['n']}): "
            f"база w=0 top1={rerank_informative_base['top1']:.4f} -> rerank top1={rerank_informative['top1']:.4f}",
            file=sys.stderr,
        )
    if tta_holdout:
        print(f"[holdout] +TTA({tta_cfg}) top1={tta_holdout['top1']:.4f} top5={tta_holdout['top5']:.4f}", file=sys.stderr)

    out = {
        "n_holdout": len(holdout),
        "base": base,
        "rerank": rerank,
        "rerank_config": rerank_cfg,
        "rerank_ungated_risk_comparison": rerank_ungated,
        "rerank_informative_subset": rerank_informative,
        "rerank_informative_subset_base": rerank_informative_base,
        "tta": tta_holdout,
        "tta_config": tta_cfg,
    }
    out_path = args.cache_dir / "holdout_results.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[holdout] записано: {out_path}", file=sys.stderr)
    return 0


# ========================================================================================
# ocr_yield — "выход" OCR: синтетика vs реальные фото (поправка оркестратора, 21.09)
# ========================================================================================
#
# Гипотеза оркестратора: синтетика СТРУКТУРНО занижает пользу OCR — эталоны кейса мелкие
# (медиана 335 px по короткой стороне, reports/f3-case-census.md), аугментатор рендерит
# из них наклоны/блики/смаз — мелкий печатный текст физически неразличим на выходе. На
# реальном телефонном фото (3024x4032, полный разрешение объектива) тот же OCR читает
# уверенно. Здесь — количественное измерение разрыва: доля фото, где OCR дал хотя бы один
# различающий токен (не "вино/красное/сухое"-подобный — см. `cv.text_rerank.has_
# distinctive_token`), и
# средняя длина распознанного, ОТДЕЛЬНО для синтетики (из кэша, дёшево) и реальных фото
# (3 контрольных из case-data/eval/queries + 7 полочных field-shots + 3 сохранённых скана
# стенда case-data/stand-scans — вычисляется здесь заново, OCR НЕ кэшируется для реальных
# фото по кэшу синтетики). Не требует ImageIndex/Qdrant — только decode+normalize+OCR.


def _ocr_yield_stats(texts: list[str], idf: dict[str, float], threshold: float) -> dict:
    n = len(texts)
    if n == 0:
        return {"n": 0}
    from cv.text_rerank import has_distinctive_token, tokenize

    nonempty = [t for t in texts if t and t.strip()]
    informative = [t for t in texts if has_distinctive_token(t, idf, threshold)]
    lens_all = [len(t or "") for t in texts]
    lens_nonempty = [len(t) for t in nonempty]
    token_counts_nonempty = [len(tokenize(t)) for t in nonempty]
    return {
        "n": n,
        "n_nonempty": len(nonempty),
        "pct_nonempty": len(nonempty) / n,
        "n_informative": len(informative),
        "pct_informative": len(informative) / n,
        "mean_len_chars_all": sum(lens_all) / n,
        "mean_len_chars_nonempty": (sum(lens_nonempty) / len(lens_nonempty)) if lens_nonempty else 0.0,
        "mean_tokens_nonempty": (sum(token_counts_nonempty) / len(token_counts_nonempty)) if token_counts_nonempty else 0.0,
    }


def _fresh_ocr_texts(paths: list[Path]) -> dict[str, dict[str, str]]:
    """decode+normalize_query (оба варианта)+read_text на реальных фото, НАПРЯМУЮ (не
    через кэш синтетики — тот ключуется по photo_id синтетического датасета). Не трогает
    ImageIndex/Qdrant — можно звать параллельно с `cache` (тот держит лок эмбеддед-Qdrant,
    этому лок не нужен вовсе)."""
    from cv import imageio
    from cv.normalize import normalize_query
    from cv.verify import LabelVerifier

    verifier = LabelVerifier()
    out: dict[str, dict[str, str]] = {}
    for path in paths:
        try:
            data = path.read_bytes()
            arr = imageio.decode_image(data)
        except Exception as exc:  # noqa: BLE001
            print(f"[ocr_yield] ПРОПУСК {path.name}: decode упал ({exc})", file=sys.stderr)
            continue
        normed = normalize_query(arr, enabled=True)
        whole = normalize_query(arr, enabled=False)
        out[path.name] = {"ocr_crop": verifier.read_text(normed), "ocr_full": verifier.read_text(whole)}
    return out


def cmd_ocr_yield(args: argparse.Namespace) -> int:
    from cv.text_rerank import distinctive_idf_threshold

    records = _read_cache(args.cache_dir / DEFAULT_CACHE_FILE)
    if not records:
        raise SystemExit("[ocr_yield] кэш пуст — сначала `cache`")
    catalog, idf = _catalog_and_idf(args)
    threshold = distinctive_idf_threshold(idf)
    print(f"[ocr_yield] порог различающего токена (медиана IDF): {threshold:.3f}", file=sys.stderr)

    out: dict[str, dict] = {"distinctive_idf_threshold": threshold, "datasets": {}}

    # 1) синтетика — из кэша, все записи (dev+holdout, вопрос "что читает OCR" не про сплит)
    for ocr_field in OCR_FIELDS:
        texts = [r.get(ocr_field, "") or "" for r in records.values()]
        out["datasets"][f"synthetic_{ocr_field}"] = _ocr_yield_stats(texts, idf, threshold)

    # 2) реальные фото: 3 контрольных + 7 полочных + 3 стенда (если есть)
    known_dir = args.case_data_dir / "eval" / "queries"
    field_dir = args.case_data_dir / "field-shots"
    stand_dir = args.case_data_dir / "stand-scans"

    known_paths = [known_dir / n for n in ("02eef911.webp", "019c68d0.jpg", "096ca74e.jpg") if (known_dir / n).is_file()]
    field_paths = sorted(p for p in field_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) if field_dir.is_dir() else []
    stand_paths = sorted(stand_dir.rglob("*.jpg")) if stand_dir.is_dir() else []

    print(f"[ocr_yield] реальные фото: {len(known_paths)} контрольных, {len(field_paths)} полочных, {len(stand_paths)} со стенда", file=sys.stderr)

    real_groups = {"real_known_eval_queries": known_paths, "real_field_shots": field_paths, "real_stand_scans": stand_paths}
    fresh_by_group: dict[str, dict[str, dict[str, str]]] = {}
    for group_name, paths in real_groups.items():
        if not paths:
            continue
        fresh = _fresh_ocr_texts(paths)
        fresh_by_group[group_name] = fresh
        for ocr_field in OCR_FIELDS:
            texts = [v[ocr_field] for v in fresh.values()]
            out["datasets"][f"{group_name}_{ocr_field}"] = _ocr_yield_stats(texts, idf, threshold)

    # то же самое, но реальные фото ВСЕ ВМЕСТЕ (10-13 шт.) — устойчивее к единичным выбросам
    all_real_fresh: dict[str, dict[str, str]] = {}
    for fresh in fresh_by_group.values():
        all_real_fresh.update(fresh)
    for ocr_field in OCR_FIELDS:
        texts = [v[ocr_field] for v in all_real_fresh.values()]
        if texts:
            out["datasets"][f"real_all_{ocr_field}"] = _ocr_yield_stats(texts, idf, threshold)

    print("\n[ocr_yield] сводка (синтетика vs реальные фото):", file=sys.stderr)
    for name, stats in out["datasets"].items():
        if stats.get("n", 0) == 0:
            continue
        print(
            f"  {name:32s} n={stats['n']:4d} непусто={stats['pct_nonempty']:.1%} "
            f"информативно={stats['pct_informative']:.1%} длина(непуст.)={stats['mean_len_chars_nonempty']:.1f}",
            file=sys.stderr,
        )

    out["raw_real_texts"] = {
        group: {name: texts for name, texts in fresh.items()} for group, fresh in fresh_by_group.items()
    }
    out_path = args.cache_dir / "ocr_yield_results.json"
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ocr_yield] записано: {out_path}", file=sys.stderr)
    return 0


# ========================================================================================
# sanity — 3 контрольных фото + 7 полочных, без подгонки
# ========================================================================================


def cmd_sanity(args: argparse.Namespace) -> int:
    from cv import imageio
    from cv.index import ImageIndex
    from cv.normalize import normalize_query
    from cv.text_rerank import distinctive_idf_threshold, rerank_top_k
    from cv.verify import LabelVerifier

    catalog, idf = _catalog_and_idf(args)
    threshold = distinctive_idf_threshold(idf)

    tune_path = args.cache_dir / "tune_results.json"
    if tune_path.is_file():
        best = _pick_recommended_config(json.loads(tune_path.read_text(encoding="utf-8")))
        k, w, ocr_field = best["k"], best["w"], best["ocr_field"]
    else:
        print("[sanity] нет tune_results.json — использую дефолт K=10 w=0.1 ocr=ocr_crop", file=sys.stderr)
        k, w, ocr_field = 10, 0.1, "ocr_crop"
    print(f"[sanity] конфигурация: K={k} w={w} ocr={ocr_field} (гейт min_token_idf={threshold:.3f})", file=sys.stderr)

    # 3 контрольных запроса кейса (case-data/eval/queries) — истина по нашему разбору
    # (визуальная проверка агентом, brief п.4): 02eef911.webp = Мускатель Массандра
    # Белый (В каталоге), 019c68d0.jpg = Табия Пино Нуар (НЕ в каталоге), 096ca74e.jpg =
    # Aristov Donum XXIV (НЕ в каталоге).
    known_queries = [
        ("02eef911.webp", "Мускатель Массандра Белый", "in_catalog"),
        ("019c68d0.jpg", "Табия Пино Нуар полусухое 2025", "not_in_catalog"),
        ("096ca74e.jpg", "Aristov Donum XXIV Брют 2023", "not_in_catalog"),
    ]
    queries_dir = args.case_data_dir / "eval" / "queries"
    field_shots_dir = args.case_data_dir / "field-shots"

    index = ImageIndex()
    verifier = LabelVerifier()
    results: list[dict[str, Any]] = []
    try:
        for fname, label, expectation in known_queries:
            path = queries_dir / fname
            if not path.is_file():
                print(f"[sanity] ПРОПУСК {fname}: файл не найден", file=sys.stderr)
                continue
            data = path.read_bytes()
            arr = imageio.decode_image(data)
            normed = normalize_query(arr, enabled=True)
            whole = normalize_query(arr, enabled=False)
            top20 = index.search(data, top_k=TOP_K_CACHE, normalize=True)
            ranked = [(m.slug, m.score) for m in top20]
            ocr_text = verifier.read_text(normed if ocr_field == "ocr_crop" else whole)
            reranked = rerank_top_k(ranked, ocr_text, catalog, idf, k=k, w=w, min_token_idf=threshold)
            results.append({
                "photo": fname, "known_label": label, "expectation": expectation,
                "ocr_text": ocr_text,
                "top5_base": [{"slug": s, "score": round(sc, 4)} for s, sc in ranked[:5]],
                "top5_rerank": [{"slug": s, "score": round(sc, 4)} for s, sc in reranked[:5]],
                "top1_score": ranked[0][1] if ranked else None,
                "gap": top20[0].gap if top20 else None,
            })

        for path in sorted(field_shots_dir.iterdir()) if field_shots_dir.is_dir() else []:
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            data = path.read_bytes()
            try:
                arr = imageio.decode_image(data)
            except Exception as exc:  # noqa: BLE001
                print(f"[sanity] ПРОПУСК {path.name}: decode упал ({exc})", file=sys.stderr)
                continue
            normed = normalize_query(arr, enabled=True)
            whole = normalize_query(arr, enabled=False)
            top20 = index.search(data, top_k=TOP_K_CACHE, normalize=True)
            ranked = [(m.slug, m.score) for m in top20]
            ocr_text = verifier.read_text(normed if ocr_field == "ocr_crop" else whole)
            reranked = rerank_top_k(ranked, ocr_text, catalog, idf, k=k, w=w, min_token_idf=threshold)
            results.append({
                "photo": path.name, "known_label": None, "expectation": "field_shot_no_ground_truth",
                "ocr_text": ocr_text,
                "top5_base": [{"slug": s, "score": round(sc, 4)} for s, sc in ranked[:5]],
                "top5_rerank": [{"slug": s, "score": round(sc, 4)} for s, sc in reranked[:5]],
                "top1_score": ranked[0][1] if ranked else None,
                "gap": top20[0].gap if top20 else None,
            })
    finally:
        index.store.close()

    for r in results:
        print(f"\n[sanity] {r['photo']} ({r['known_label'] or 'полка, истина неизвестна'})", file=sys.stderr)
        print(f"  OCR: {r['ocr_text']!r}", file=sys.stderr)
        print(f"  top5 база:    {[c['slug'] for c in r['top5_base']]}", file=sys.stderr)
        print(f"  top5 rerank:  {[c['slug'] for c in r['top5_rerank']]}", file=sys.stderr)

    out_path = args.cache_dir / "sanity_results.json"
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[sanity] записано: {out_path}", file=sys.stderr)
    return 0


# ========================================================================================
# latency — задержка по стадиям на этой машине (Mac)
# ========================================================================================


def cmd_latency(args: argparse.Namespace) -> int:
    from cv import imageio
    from cv.index import ImageIndex
    from cv.normalize import normalize_query
    from cv.text_rerank import rerank_top_k
    from cv.verify import LabelVerifier

    items = load_items(args.photos_dir)
    sample = items[args.skip : args.skip + args.n]
    print(f"[latency] выборка: {len(sample)} фото (skip={args.skip})", file=sys.stderr)

    index = ImageIndex()
    verifier = LabelVerifier()
    verifier._load()  # прогрев OCR-движка до замера

    def stats(v: list[float]) -> dict:
        s = sorted(v)
        n = len(s)
        return {
            "n": n, "mean_ms": round(sum(s) / n, 1), "p50_ms": round(s[n // 2], 1),
            "p95_ms": round(s[min(n - 1, int(n * 0.95))], 1), "max_ms": round(s[-1], 1),
        }

    try:
        decode_t, normalize_t, ann_t, ocr_crop_t, ocr_full_t, rerank_t = [], [], [], [], [], []
        arrs = []
        for it in sample:
            data = it.path.read_bytes()
            t0 = time.perf_counter()
            arr = imageio.decode_image(data)
            t1 = time.perf_counter()
            normed = normalize_query(arr, enabled=True)
            t2 = time.perf_counter()
            decode_t.append((t1 - t0) * 1000)
            normalize_t.append((t2 - t1) * 1000)
            arrs.append((arr, normed))

        # embed — ЦЕЛЕНАПРАВЛЕННО через encoder.benchmark(use_cache бы обойдён внутри) —
        # иначе .embed_cache/ (много прошлых прогонов на этих же файлах) занижает цифру
        # до скорости dict-lookup, а не реального инференса (encoder.py сам об этом
        # предупреждает в докстринге `benchmark()`).
        embed_bench = index.encoder.benchmark([n for _, n in arrs], n=len(arrs))
        print(f"[latency] embed (cache bypassed): {embed_bench}", file=sys.stderr)

        # ANN-only (векторы уже посчитаны encoder'ом выше — encode() с кэшем теперь их найдёт мгновенно)
        for arr, normed in arrs:
            vec = index.encoder.encode(normed)
            t0 = time.perf_counter()
            index.store.search(index.collection, vec, top_k=max(TOP_K_CACHE * 8, TOP_K_CACHE + 10))
            ann_t.append((time.perf_counter() - t0) * 1000)

        # OCR — оба варианта, движок уже прогрет (verifier._load() выше)
        for it, (arr, normed) in zip(sample, arrs):
            whole = normalize_query(arr, enabled=False)
            t0 = time.perf_counter()
            verifier.read_text(normed)
            t1 = time.perf_counter()
            verifier.read_text(whole)
            t2 = time.perf_counter()
            ocr_crop_t.append((t1 - t0) * 1000)
            ocr_full_t.append((t2 - t1) * 1000)

        # text_rerank compute — чистый python/rapidfuzz, на реальных top-20 этой выборки
        from cv.text_rerank import distinctive_idf_threshold

        catalog, idf = _catalog_and_idf(args)
        threshold = distinctive_idf_threshold(idf)
        for it, (arr, normed) in zip(sample, arrs):
            matches = index.search(it.path.read_bytes(), top_k=TOP_K_CACHE, normalize=True)
            ranked = [(m.slug, m.score) for m in matches]
            ocr_text = verifier.read_text(normed)
            t0 = time.perf_counter()
            rerank_top_k(ranked, ocr_text, catalog, idf, k=10, w=0.1, min_token_idf=threshold)
            rerank_t.append((time.perf_counter() - t0) * 1000)
    finally:
        index.store.close()

    out = {
        "n": len(sample),
        "decode_ms": stats(decode_t),
        "normalize_crop_ms": stats(normalize_t),
        "embed_cache_bypassed": embed_bench,
        "ann_search_only_ms": stats(ann_t),
        "ocr_crop_ms": stats(ocr_crop_t),
        "ocr_full_ms": stats(ocr_full_t),
        "text_rerank_compute_ms": stats(rerank_t),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    out_path = args.cache_dir / "latency_results.json"
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[latency] записано: {out_path}", file=sys.stderr)
    return 0


# ========================================================================================
# CLI
# ========================================================================================


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--photos-dir", type=Path, default=DEFAULT_PHOTOS_DIR)
        p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
        p.add_argument("--seed", type=int, default=se.DEFAULT_SEED)
        p.add_argument("--holdout-frac", type=float, default=se.DEFAULT_HOLDOUT_FRAC)
        p.add_argument("--catalog-csv", type=Path, default=CASE_DATA_DIR_DEFAULT / "strapi_output0709.csv")
        p.add_argument("--case-data-dir", type=Path, default=CASE_DATA_DIR_DEFAULT)

    p_cache = sub.add_parser("cache", help="построить/дополнить кэш CV top-20 + OCR")
    common(p_cache)
    p_cache.add_argument("--limit", type=int, default=None, help="ограничить число НОВЫХ фото за этот вызов")
    p_cache.set_defaults(func=cmd_cache)

    p_tune = sub.add_parser("tune", help="свип K x w x ocr_field на dev")
    common(p_tune)
    p_tune.set_defaults(func=cmd_tune)

    p_tta = sub.add_parser("tta", help="TTA: слияние max/mean по кропам на dev")
    common(p_tta)
    p_tta.set_defaults(func=cmd_tta)

    p_holdout = sub.add_parser("holdout", help="финальные цифры на holdout")
    common(p_holdout)
    p_holdout.set_defaults(func=cmd_holdout)

    p_yield = sub.add_parser("ocr_yield", help="выход OCR: синтетика vs реальные фото")
    common(p_yield)
    p_yield.set_defaults(func=cmd_ocr_yield)

    p_sanity = sub.add_parser("sanity", help="3 контрольных + 7 полочных фото")
    common(p_sanity)
    p_sanity.set_defaults(func=cmd_sanity)

    p_latency = sub.add_parser("latency", help="задержка по стадиям на этой машине")
    common(p_latency)
    p_latency.add_argument("--n", type=int, default=60)
    p_latency.add_argument("--skip", type=int, default=200, help="пропустить первые N фото (уже засвечены в других прогонах)")
    p_latency.set_defaults(func=cmd_latency)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
