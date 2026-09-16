#!/usr/bin/env python3
"""qa/case_ref_triage.py — триаж качества эталонных фото (задача 1a, дополнение
оркестратора 2026-09-16 поверх agents/F3-census.md): среди эталонов кейса много шума —
лайфстайл-кадры (человек с бокалом/бутылкой), виноградники-пейзажи, интерьеры — не
снимки бутылки/этикетки. Честная перепись поштучно: по каждому эталону — класс сцены,
по каждому слагу — usable:true/false, БЕЗ притворного покрытия.

Метод — SigLIP2 zero-shot (та же модель, что уже кэширована и используется индексом,
`cv/encoder.py`/`CV_MODEL`) — text tower модели даёт классификацию БЕЗ обучаемого
классификатора и БЕЗ разметки (которой нет и не будет до приезда полевых фото). Это
ДИАГНОСТИКА качества поставки, не часть search-пайплайна — packages/cv не меняется ни
байтом (модель используется штатно read-only; эмбеддинг-кэш перенаправлен в свою зону
qa/, см. --embed-cache-dir).

Классы (заданы оркестратором): label_closeup / bottle_in_scene / person_with_wine /
vineyard_scenery / interior_marketing / logo_graphic / other_noise.

`usable` НЕ равен "top_class из двух usable-классов" — так считало первую версию
скрипта, и результат оказался методологической ошибкой: top1-vs-top2 margin среди ВСЕХ
7 классов почти всегда сравнивает bottle_in_scene ПРОТИВ label_closeup (оба usable!) —
эта пара соседняя и почти всегда самая близкая, так что "спорный" по общему margin'у в
основном означает "не уверены МЕЖДУ ДВУМЯ ХОРОШИМИ вариантами", а не "фото вообще не
годится". Ретроспективная проверка на реальном прогоне (см. reports/f3-case-census.md):
из 620 слагов с usable=False по старой формуле 508 честно были bottle_in_scene/
label_closeup, просто с margin в нижнем квартиле среди ВСЕХ классов — не значит непригодны.

Правильная величина — `usability_margin = max(score среди usable-классов) - max(score
среди НЕ-usable-классов)`: `usable = usability_margin > 0` (естественная граница у нуля,
не подобранная константа). На реальных данных это даёт 1973/1982 (99.5%) usable файлов,
и ВСЕ 9 файлов с usability_margin <= 0 при ручной визуальной проверке подтвердились как
настоящий шум (виноградник, афиша фестиваля, лайфстайл-сцена) — см. отчёт. `ambiguous` —
теперь про БЛИЗОСТЬ К ЭТОЙ ГРАНИЦЕ (|usability_margin| в нижнем квартиле по модулю), а
не про то, какой из двух usable-классов победил.

Классификация — RAW фото (БЕЗ detect_label_region/unwarp): цель этой проверки — понять,
ЧТО на фото (сцена целиком), а обрезка по detect_label_region заранее исказила бы
пейзаж/человека в мусорный кроп и сделала бы классификацию бессмысленной.

Cross-checks (дёшевы, НЕ решающий голос, доп. колонки отчёта):
  - `cv.normalize._foreground_bbox()` на СЫРОМ фото — None -> классический детектор не
    нашёл силуэт бутылки на однородном фоне (сигнал В ПОЛЬЗУ шума для студийных эталонов
    каталога, для которых детектор и проектировался).
  - разрешение/аспект — из уже декодированного массива (`image.shape`), без второго
    чтения файла.

Каждый ДИСТИНКТНЫЙ chosen-файл классифицируется РОВНО ОДИН РАЗ (near-dup семьи делят
физический файл — гонять инференс дважды на тех же байтах бессмысленно), результат
транслируется на все слаги, разделяющие файл.

Порог |usability_margin| для "спорный" (--margin-percentile, дефолт 25 = нижний квартиль
|usability_margin| САМОГО ЭТОГО прогона) НЕ ОТКАЛИБРОВАН на разметке (её нет) — эвристика
первого прохода. Абсолютное число тут в принципе неприменимо без предварительного
прогона: масштаб SigLIP2-margin заранее неизвестен (на смоук-прогоне c ДРУГОЙ, ошибочной
формулой margin p50 был ~0.01; на usability_margin по факту p50 ~0.056) — процентиль
распределения САМОГО прогона гарантирует нетривиальное разбиение независимо от масштаба.
`--margin-threshold` даёт АБСОЛЮТНЫЙ порог явно, если он всё же понадобится после ручной
калибровки. Финальная калибровка — после того как Вячеслав посмотрит
case-data/triage_samples/<class>/ (--samples-per-class).

Запускать ТОЛЬКО через venv пакета cv (torch/transformers):

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    packages/cv/.venv/bin/python qa/case_ref_triage.py \\
        --case-data-dir /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data

Требует case-data/slug_refs.json (из qa/case_census.py --write). Дописывает В НЕГО:
top-level `ref_quality` (файл -> класс/скоры/margin/спорный/detector_fallback/w×h) и
`usable` внутри каждой записи `mapping[slug]`. mapping/candidates/match_method/прочее —
НЕ трогает, только добавляет поля.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent

DEFAULT_CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_SUBPATH = Path("prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads")
DEFAULT_EMBED_CACHE_DIR = _QA_DIR / "case-census-run" / "triage-embed-cache"

SAMPLE_SEED = 20260916
DEFAULT_SAMPLES_PER_CLASS = 12
DEFAULT_MARGIN_PERCENTILE = 25.0  # нижний квартиль margin'ов ЭТОГО прогона -> "спорный"

USABLE_CLASSES = {"label_closeup", "bottle_in_scene"}

# Класс -> промпты-ансамбль (рус+англ, несколько формулировок — усредняем L2-нормированные
# text-эмбеддинги, стандартный приём zero-shot CLIP/SigLIP-классификации).
CLASS_PROMPTS: dict[str, list[str]] = {
    "label_closeup": [
        "a close-up photo of a wine bottle label",
        "close-up product photo of a wine label, studio shot",
        "крупный план этикетки бутылки вина",
        "студийное фото этикетки вина крупным планом",
    ],
    "bottle_in_scene": [
        "a photo of a whole wine bottle on a plain background",
        "product photo of a wine bottle",
        "фотография бутылки вина целиком на однотонном фоне",
        "товарное фото бутылки вина",
    ],
    "person_with_wine": [
        "a person holding a glass of wine",
        "a person holding a wine bottle",
        "человек держит бокал вина",
        "человек с бутылкой вина в руках",
    ],
    "vineyard_scenery": [
        "a vineyard landscape with rows of grapevines",
        "a photo of grapes growing on a vine",
        "пейзаж виноградника с рядами виноградной лозы",
        "виноградник, поле с лозой",
    ],
    "interior_marketing": [
        "an interior photo of a wine shop or restaurant",
        "a marketing banner or advertisement with a wine bottle and text",
        "интерьер винного магазина или ресторана",
        "рекламный баннер с бутылкой вина и текстом",
    ],
    "logo_graphic": [
        "a company logo graphic on a plain background",
        "a vector logo icon",
        "логотип компании на однотонном фоне",
        "векторный значок или логотип",
    ],
    "other_noise": [
        "a random photo unrelated to a wine bottle",
        "a screenshot of a document, chart or map",
        "случайное фото, не связанное с бутылкой вина",
        "скриншот документа, диаграммы или карты",
    ],
}


def _l2norm(vec):
    import numpy as np

    n = np.linalg.norm(vec)
    return vec / n if n > 0 else vec


def build_class_text_embeddings(encoder) -> dict[str, Any]:
    """Прогоняет промпты через text tower ТОЙ ЖЕ модели, что `encoder` (image tower) —
    один load, обе башни. `cv.encoder.SiglipEncoder` не даёт text API готовым (у него
    только `encode()` — картинка), поэтому здесь напрямую используются `encoder._model`/
    `encoder._processor` ПОСЛЕ `encoder._load()` (та же модель, не второй чекпойнт) —
    read-only вызов уже существующих методов, packages/cv не меняется."""
    import numpy as np
    import torch

    encoder._load()
    model, processor, device = encoder._model, encoder._processor, encoder.device

    out: dict[str, Any] = {}
    for cls, prompts in CLASS_PROMPTS.items():
        inputs = processor(text=prompts, padding="max_length", return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            result = model.get_text_features(**inputs)
        feats = result if isinstance(result, torch.Tensor) else result.pooler_output
        arr = feats.to("cpu", dtype=torch.float32).numpy()
        normed = np.stack([_l2norm(v) for v in arr])
        out[cls] = _l2norm(normed.mean(axis=0))
    return out


def classify_image(image_arr, encoder, class_embeds: dict[str, Any]) -> dict[str, Any]:
    import numpy as np

    img_vec = np.array(encoder.encode(image_arr, use_cache=True))
    scores = {cls: float(np.dot(img_vec, v)) for cls, v in class_embeds.items()}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top1_cls, top1_score = ranked[0]
    top2_score = ranked[1][1] if len(ranked) > 1 else top1_score

    best_usable = max(scores[c] for c in USABLE_CLASSES)
    not_usable_classes = [c for c in scores if c not in USABLE_CLASSES]
    best_not_usable = max(scores[c] for c in not_usable_classes) if not_usable_classes else best_usable
    usability_margin = best_usable - best_not_usable

    return {
        "scores": {k: round(v, 4) for k, v in scores.items()},
        "top_class": top1_cls,
        "top_score": round(top1_score, 4),
        "class_margin": round(top1_score - top2_score, 4),  # top1 vs top2 СРЕДИ ВСЕХ 7 классов — информативно, НЕ решает usable
        "usability_margin": round(usability_margin, 4),  # лучший usable vs лучший НЕ-usable — ЭТО решает usable
    }


def detector_found_bottle(image_arr) -> bool:
    """True, если классический детектор (`cv.normalize._foreground_bbox`) нашёл силуэт
    на однородном фоне — cross-check (a) заказа оркестратора. Не решающий голос."""
    from cv.normalize import _foreground_bbox

    try:
        return _foreground_bbox(image_arr) is not None
    except Exception:  # noqa: BLE001 — cross-check не должен ронять весь прогон
        return False


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case-data-dir", type=Path, default=DEFAULT_CASE_DATA_DIR)
    parser.add_argument(
        "--margin-percentile", type=float, default=DEFAULT_MARGIN_PERCENTILE,
        help="нижний перцентиль margin ЭТОГО прогона -> 'спорный' (игнорируется, если задан --margin-threshold)",
    )
    parser.add_argument(
        "--margin-threshold", type=float, default=None,
        help="АБСОЛЮТНЫЙ порог margin (обходит перцентильный расчёт) — для ручной калибровки после triage_samples/",
    )
    parser.add_argument("--samples-per-class", type=int, default=DEFAULT_SAMPLES_PER_CLASS)
    parser.add_argument("--embed-cache-dir", type=Path, default=DEFAULT_EMBED_CACHE_DIR)
    parser.add_argument("--no-samples", action="store_true", help="не создавать case-data/triage_samples/")
    parser.add_argument("--limit", type=int, default=None, help="ограничить число файлов (smoke-прогон)")
    args = parser.parse_args(argv)

    case_dir: Path = args.case_data_dir
    uploads_dir = case_dir / UPLOADS_SUBPATH
    slug_refs_path = case_dir / "slug_refs.json"
    samples_dir = case_dir / "triage_samples"

    slug_refs = json.loads(slug_refs_path.read_text(encoding="utf-8"))
    mapping: dict[str, Any] = slug_refs["mapping"]

    # Дистинктные chosen-файлы -> какие слаги их разделяют (near-dup семьи делят файл).
    file_to_slugs: dict[str, list[str]] = {}
    for slug, entry in mapping.items():
        fn = entry.get("chosen")
        if fn:
            file_to_slugs.setdefault(fn, []).append(slug)
    distinct_files = sorted(file_to_slugs)
    if args.limit:
        distinct_files = distinct_files[: args.limit]

    from cv import imageio
    from cv.encoder import SiglipEncoder

    encoder = SiglipEncoder(cache_dir=args.embed_cache_dir)  # НЕ packages/cv/.embed_cache — своя зона
    t0 = time.perf_counter()
    class_embeds = build_class_text_embeddings(encoder)
    print(f"[case_ref_triage] модель загружена, {len(CLASS_PROMPTS)} классов, "
          f"{sum(len(p) for p in CLASS_PROMPTS.values())} промптов, {time.perf_counter()-t0:.1f}с", file=sys.stderr)

    ref_quality: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    t0 = time.perf_counter()
    for i, fn in enumerate(distinct_files):
        path = uploads_dir / fn
        try:
            data = path.read_bytes()
            arr = imageio.decode_image(data)
        except Exception as exc:  # noqa: BLE001 — битый файл не должен ронять весь прогон
            errors.append({"file": fn, "error": str(exc)})
            ref_quality[fn] = {
                "top_class": "other_noise", "top_score": None, "class_margin": None,
                "usability_margin": None, "usable": False, "ambiguous": True,
                "decode_error": str(exc), "detector_fallback": None, "width": None, "height": None,
            }
            continue

        h, w = arr.shape[:2]
        result = classify_image(arr, encoder, class_embeds)
        fallback = not detector_found_bottle(arr)
        ref_quality[fn] = {
            **result,
            "detector_fallback": fallback,
            "width": int(w),
            "height": int(h),
            "short_side": int(min(w, h)),
            "aspect_ratio": round(w / h, 3) if h else None,
        }
        if (i + 1) % 200 == 0:
            elapsed = time.perf_counter() - t0
            print(f"[case_ref_triage] {i+1}/{len(distinct_files)} файлов, {elapsed:.1f}с "
                  f"({elapsed/(i+1)*1000:.0f}мс/файл)", file=sys.stderr)

    elapsed = time.perf_counter() - t0

    # --- usable = знак usability_margin (естественная граница у нуля, см. докстринг).
    # --- "спорный" — БЛИЗОСТЬ к этой границе: |usability_margin| в нижнем перцентиле
    # РАСПРЕДЕЛЕНИЯ этого прогона (или абсолютный порог, если --margin-threshold задан
    # явно). decode_error файлы величины не несут — всегда спорные.
    usability_margins = [rq["usability_margin"] for rq in ref_quality.values() if rq.get("usability_margin") is not None]
    abs_margins = [abs(m) for m in usability_margins]
    if args.margin_threshold is not None:
        resolved_threshold = args.margin_threshold
        threshold_mode = "absolute"
    else:
        resolved_threshold = _percentile(abs_margins, args.margin_percentile) or 0.0
        threshold_mode = f"percentile_{args.margin_percentile:g}_of_abs_usability_margin"
    for rq in ref_quality.values():
        um = rq.get("usability_margin")
        rq["usable"] = um is not None and um > 0 and not rq.get("decode_error")
        rq["ambiguous"] = um is None or abs(um) <= resolved_threshold

    # usable на слаг: транслируем с файла на все слаги, которые его разделяют.
    for fn, slugs in file_to_slugs.items():
        rq = ref_quality.get(fn, {})
        for slug in slugs:
            mapping[slug]["usable"] = bool(rq.get("usable"))
            mapping[slug]["ref_quality_class"] = rq.get("top_class")
    for slug, entry in mapping.items():
        if "usable" not in entry:
            entry["usable"] = False  # нет chosen-файла вовсе -> заведомо не usable

    slug_refs["ref_quality"] = ref_quality
    slug_refs["ref_quality_meta"] = {
        "method": "SigLIP2 zero-shot (cv.encoder.SiglipEncoder image tower + text tower, "
                  "CV_MODEL env, packages/cv venv)",
        "classes": list(CLASS_PROMPTS.keys()),
        "usable_classes": sorted(USABLE_CLASSES),
        "usable_rule": "usability_margin = max(score usable-классов) - max(score НЕ-usable) > 0",
        "usability_margin_ambiguous_threshold_resolved": round(resolved_threshold, 4),
        "usability_margin_ambiguous_threshold_mode": threshold_mode,
        "threshold_calibrated": False,
        "distinct_files_classified": len(distinct_files),
        "decode_errors": len(errors),
        "elapsed_s": round(elapsed, 1),
    }
    slug_refs_path.write_text(json.dumps(slug_refs, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    # --- сводка по классам + честное покрытие ---
    from collections import Counter

    class_counts = Counter(rq["top_class"] for rq in ref_quality.values())
    ambiguous_count = sum(1 for rq in ref_quality.values() if rq.get("ambiguous"))
    not_usable_by_margin = sum(1 for rq in ref_quality.values() if rq.get("usability_margin") is not None and rq["usability_margin"] <= 0)
    usable_slugs = sum(1 for e in mapping.values() if e.get("usable"))

    summary = {
        "distinct_files": len(distinct_files),
        "class_counts": dict(class_counts),
        "distinct_files_not_usable": not_usable_by_margin,
        "ambiguous_files": ambiguous_count,
        "usability_margin_ambiguous_threshold_resolved": round(resolved_threshold, 4),
        "usability_margin_ambiguous_threshold_mode": threshold_mode,
        "decode_errors": len(errors),
        "usability_margin_p10": _percentile(usability_margins, 10),
        "usability_margin_p25": _percentile(usability_margins, 25),
        "usability_margin_p50": _percentile(usability_margins, 50),
        "slugs_total": len(mapping),
        "slugs_usable": usable_slugs,
        "slugs_usable_pct": round(usable_slugs / len(mapping) * 100, 1) if mapping else None,
        "elapsed_s": round(elapsed, 1),
    }

    # --- выборка для ручной проверки: case-data/triage_samples/<class>/ ---
    if not args.no_samples:
        rng = random.Random(SAMPLE_SEED)
        by_class: dict[str, list[str]] = {}
        for fn, rq in ref_quality.items():
            by_class.setdefault(rq["top_class"], []).append(fn)
        samples_dir.mkdir(parents=True, exist_ok=True)
        sample_manifest: dict[str, list[str]] = {}
        for cls, files in by_class.items():
            cls_dir = samples_dir / cls
            cls_dir.mkdir(parents=True, exist_ok=True)
            for old in cls_dir.iterdir():  # предыдущий прогон — пересобираем набор начисто
                if old.is_symlink() or old.is_file():
                    old.unlink()
            chosen = files if len(files) <= args.samples_per_class else rng.sample(files, args.samples_per_class)
            sample_manifest[cls] = sorted(chosen)
            for fn in chosen:
                target = uploads_dir / fn
                link = cls_dir / fn
                try:
                    link.symlink_to(target)
                except FileExistsError:
                    pass
        (samples_dir / "MANIFEST.json").write_text(
            json.dumps({"seed": SAMPLE_SEED, "per_class": sample_manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary["samples_written_to"] = str(samples_dir)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
