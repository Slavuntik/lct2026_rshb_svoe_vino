#!/usr/bin/env python3
"""qa/analyze_case_synthetic_baseline.py — объединение и стратифицированный анализ
полномасштабного синтетического baseline (поручение оркестратора, ночь 2026-09-17,
поверх agents/F3-census.md): два прогона `qa/scan_eval.py --mode rich` (батчи A/B, по
~991 фото каждый — разбито ради времени одного вызова, см. reports/f3-case-census.md)
против ОДНОГО живого `apps/api` (реальный CV + реальный OCR-верификатор, индекс
case-20260917).

Считает ОБЩИЕ метрики (объединение records обоих батчей — те же функции, что и сам
scan_eval.py: `compute_metrics`/`_macro_f1`, не переизобретаются) + разрезы, которые сам
scan_eval.py не умеет "из коробки" (they специфичны для этого датасета):
  - near-dup семьи (case-data/families.json) vs остальные;
  - fallback-детекторные слаги (packages/cv/data/label_detector_case-20260917.json,
    посчитан G3 при сборке индекса) vs "чистые";
  - "сырой top-1" — доля записей, где true_slug совпадает с top5_slugs[0] (`matches[0]`
    официального rich-ответа) НЕЗАВИСИМО от решения confident/not_in_catalog — этот
    список приходит от API ВСЕГДА (contracts v0.4.3), даже когда `slug=null`. Официальный
    match-rate меряет систему С ГЕЙТОМ (что видит пользователь); "сырой top-1" меряет
    КАЧЕСТВО РАНЖИРОВАНИЯ ANN отдельно от калибровки порога — обе цифры важны и НЕ
    заменяют друг друга (см. докстринг RichApiPredictor и qa/acceptance.md, «Порядок дня
    датасета» (д), про риск одного порога на сыром score).

Пишет: qa/scan-eval-runs/case-20260917-synthetic-baseline/report.json (объединённые
records + метрики + разрезы) и eval_report_snapshot.json (схема
apps/api/app/cv/eval_report.py, для CV_EVAL_REPORT_PATH).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_QA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_QA_DIR))

from scan_eval import (  # noqa: E402
    RunRecord,
    build_report,
    compute_metrics,
    eval_report_snapshot,
    top_confusions,
    write_eval_report_snapshot,
    write_report,
)

CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
FAMILIES_PATH = CASE_DATA_DIR / "families.json"
LABEL_DETECTOR_PATH = Path(
    "/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/data/label_detector_case-20260917.json"
)


def load_records(report_json_path: Path) -> tuple[list[RunRecord], dict[str, Any]]:
    data = json.loads(report_json_path.read_text(encoding="utf-8"))
    records = [RunRecord(**r) for r in data["records"]]
    return records, data


def raw_top1_rate(records: list[RunRecord]) -> dict[str, Any]:
    """Доля записей, где true_slug — ПЕРВЫЙ элемент top5_slugs (raw ANN-ранжирование из
    `matches`, НЕЗАВИСИМО от not_in_catalog-гейта). Отдельно от match_rate официального
    (см. докстринг модуля)."""
    ok = [r for r in records if r.error is None and r.top5_slugs]
    if not ok:
        return {"n": 0, "raw_top1_rate": None, "raw_top1_hits": 0}
    hits = sum(1 for r in ok if r.top5_slugs[0] == r.true_slug)
    return {"n": len(ok), "raw_top1_hits": hits, "raw_top1_rate": round(hits / len(ok), 4)}


def stratum_report(name: str, records: list[RunRecord]) -> dict[str, Any]:
    metrics = compute_metrics(records)
    raw = raw_top1_rate(records)
    return {
        "stratum": name,
        "n_records": len(records),
        "match_rate": metrics["match_rate"],
        "match_rate_top5": metrics["match_rate_top5"],
        "f1_top1": metrics["f1_top1"],
        "f1_top5": metrics["f1_top5"],
        "p50_ms": metrics["p50_ms"],
        "p95_ms": metrics["p95_ms"],
        "n_errors": metrics["n_errors"],
        "raw_top1_rate_ungated": raw["raw_top1_rate"],
    }


def main() -> int:
    batch_a = Path(
        "/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/"
        "scratchpad/scan-eval-batchA/report.json"
    )
    batch_b = Path(
        "/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/"
        "scratchpad/scan-eval-batchB/report.json"
    )
    out_dir = _QA_DIR / "scan-eval-runs" / "case-20260917-synthetic-baseline"
    eval_report_path = out_dir / "eval_report_snapshot.json"

    records_a, meta_a = load_records(batch_a)
    records_b, meta_b = load_records(batch_b)
    all_records = records_a + records_b
    assert meta_a["index_version"] == meta_b["index_version"] == "case-20260917", (
        meta_a["index_version"], meta_b["index_version"]
    )

    families = json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))
    family_slugs: set[str] = set()
    for fam in families.values():
        family_slugs.update(fam.get("slugs", []))

    label_detector = json.loads(LABEL_DETECTOR_PATH.read_text(encoding="utf-8"))
    fallback_slugs: set[str] = set(label_detector.get("fallback_slugs", []))

    family_records = [r for r in all_records if r.true_slug in family_slugs]
    non_family_records = [r for r in all_records if r.true_slug not in family_slugs]
    fallback_records = [r for r in all_records if r.true_slug in fallback_slugs]
    clean_records = [r for r in all_records if r.true_slug not in fallback_slugs]

    overall = stratum_report("overall (все 1982)", all_records)
    breakdown = [
        overall,
        stratum_report(f"near-dup семьи ({len(family_slugs & {r.true_slug for r in all_records})} слагов)", family_records),
        stratum_report("НЕ near-dup (остальные)", non_family_records),
        stratum_report(f"fallback-детекторные ({len(fallback_slugs & {r.true_slug for r in all_records})} слагов)", fallback_records),
        stratum_report("чистые (детектор нашёл силуэт)", clean_records),
    ]

    combined_report = build_report(
        mode="rich",
        api_url=meta_a["api_url"],
        split="all",
        seed=meta_a["seed"],
        holdout_frac=meta_a["holdout_frac"],
        photos_dir=Path(f"{meta_a['photos_dir']} + {meta_b['photos_dir']} (батчи A+B, синтетика seed=20260917)"),
        items=[],  # объединённый отчёт не хранит EvalItem, только records (см. build_report ниже)
        records=all_records,
        load_warnings=[*meta_a["warnings"], *meta_b["warnings"]],
        run_warnings=[
            "ПОЛНОМАСШТАБНЫЙ СИНТЕТИЧЕСКИЙ baseline на ЭТИХ ЖЕ эталонах (self-match-style, "
            "seed=20260917) — НЕ полевые фото. Полевой замер — по приезду публичного датасета "
            "(qa/acceptance.md §9).",
            "Прогнан двумя батчами (A/B, по ~991 фото) ради времени одного вызова, объединено "
            "этим скриптом — метрики те же функции, что scan_eval.py.",
        ],
        index_version=meta_a["index_version"],
    )
    combined_report["n_items_loaded"] = len(all_records)
    combined_report["case_breakdown"] = breakdown
    combined_report["raw_top1_rate_ungated_overall"] = raw_top1_rate(all_records)
    combined_report["not_in_catalog_gate_note"] = (
        "match_rate официальный меряет систему С ГЕЙТОМ confident/not_in_catalog "
        "(contracts v0.4.5, по gap, не по абсолютному score) — что видит пользователь. "
        "raw_top1_rate_ungated меряет ТОЛЬКО качество ранжирования ANN (matches[0]), "
        "независимо от решения гейта. Расхождение между ними — прямой сигнал калибровки "
        "порога, не баг раннера (см. reports/f3-case-census.md)."
    )

    json_path, md_path = write_report(combined_report, out_dir)
    snapshot_path = write_eval_report_snapshot(combined_report, eval_report_path)

    print(json.dumps({"overall": overall, "written": [str(json_path), str(md_path), str(snapshot_path)]},
                      ensure_ascii=False, indent=2))
    for row in breakdown:
        print(f"{row['stratum']:45} n={row['n_records']:5} match={row['match_rate']:.3f} "
              f"f1top1={row['f1_top1']:.3f} f1top5={row['f1_top5']:.3f} "
              f"raw_top1={row['raw_top1_rate_ungated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
