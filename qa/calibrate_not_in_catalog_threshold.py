#!/usr/bin/env python3
"""qa/calibrate_not_in_catalog_threshold.py — калибровка CV_CONFIDENT_SCORE_THRESHOLD на
impostor-холдауте (агент F2, поручение оркестратора после ревью 04).

Контекст: ревью 04 нашло, что порог 0.55 (apps/api/app/config.py::cv_confident_score_
threshold) почти никогда не срабатывает — синтетический baseline F2 наблюдал top1_score
0.8-1.0 даже у ПРАВИЛЬНЫХ совпадений (`mean_top1_score=0.889`, `qa/scan-eval-runs/
synthetic-cv-index-baseline/`). Если импостеры (вина, которых точно нет в индексе) тоже
рутинно набирают score выше 0.55 — ветка not_in_catalog (case.md: "оценивается особенно
высоко") практически никогда не срабатывает, и система будет уверенно называть чужое вино
существующим slug'ом вместо честного "не найдено".

Метод — классический ROC-стиль на двух распределениях top1_score:
  - ПОЗИТИВНЫЙ набор (`qa/synthetic/photos` + `labels.csv`, уже готов из baseline F2) — вина,
    которые ЕСТЬ в калибровочном индексе; FNR(t) = доля с top1_score < t (система вредно
    перестраховалась бы и на самом деле поймала бы этого).
  - ИМПОСТОР-набор (`qa/gen_impostor_photos.py`) — вина, которых В КАЛИБРОВОЧНОМ ИНДЕКСЕ
    ТОЧНО НЕТ (held-out реальные + синтетические выдуманные этикетки); FPR(t) = доля с
    top1_score >= t (система бы уверенно подсунула чужой slug вместо честного "не найдено").

ВАЖНО: калибровочный индекс — ОТДЕЛЬНЫЙ от baseline-индекса F2 (46 позиций вместо 56,
`qa/synthetic/impostor-calibration/cv-index/`, НЕ `qa/synthetic/cv-index/`) — held-out
слагам физически неоткуда взяться в baseline-индексе, иначе тест был бы бессмысленным
(self-match вместо "не в каталоге"). Позитивный набор пересчитан ПРОТИВ ЭТОГО индекса
(не переиспользованы старые скоры baseline) — набор конкурентов в ANN слегка другой (на 10
эталонов меньше), сравнение иначе было бы нечестным.

Запускать ТОЛЬКО через venv пакета cv:
    packages/cv/.venv/bin/python qa/gen_impostor_photos.py       # сначала сгенерировать
    packages/cv/.venv/bin/python qa/calibrate_not_in_catalog_threshold.py

Пишет qa/scan-eval-runs/not-in-catalog-calibration/{report.json,report.md}.

Пороговая арифметика (compute_fpr/compute_fnr/sweep_thresholds/recommend_threshold) — чистые
функции без torch/opencv, тестируются под обычным qa/.venv (qa/test_synthetic_baseline.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_CV_PKG_DIR = _REPO_ROOT / "packages" / "cv"
_CV_CLI = _CV_PKG_DIR / ".venv" / "bin" / "cv"

_IMPOSTOR_DIR = _QA_DIR / "synthetic" / "impostor-calibration"
_REFS_CSV = _IMPOSTOR_DIR / "refs.csv"
_IMPOSTOR_PHOTOS_DIR = _IMPOSTOR_DIR / "photos"
_IMPOSTOR_LABELS_CSV = _IMPOSTOR_DIR / "labels.csv"
_CALIBRATION_DATA_DIR = _IMPOSTOR_DIR / "cv-index" / "data"

_POSITIVE_PHOTOS_DIR = _QA_DIR / "synthetic" / "photos"
_POSITIVE_LABELS_CSV = _QA_DIR / "synthetic" / "labels.csv"

# Реиспользуем embed-кэш основного baseline-прогона (F2) — ключ по sha256 пиксельных байт +
# модель (cv/encoder.py), безопасно шарить между разными индексами: build-index холдаута
# переиспользует уже посчитанные эмбеддинги для 46 общих эталонов, платим только за
# holdout-специфичные запросы.
_SHARED_EMBED_CACHE_DIR = _QA_DIR / "synthetic" / "cv-index" / "embed_cache"

_OUT_DIR = _QA_DIR / "scan-eval-runs" / "not-in-catalog-calibration"

BUILD_SEED = 0
BUILD_VIEWS = 24
INDEX_VERSION = "f2-impostor-calibration-46"

# Текущий плейсхолдер в apps/api/app/config.py (CV_CONFIDENT_SCORE_THRESHOLD) — сверяем сеткой.
CURRENT_THRESHOLD = 0.55
THRESHOLD_GRID = [round(0.30 + 0.025 * i, 3) for i in range(29)]  # 0.30 .. 1.00 шагом 0.025

os.environ.setdefault("CV_DATA_DIR", str(_CALIBRATION_DATA_DIR))
os.environ.setdefault("CV_EMBED_CACHE_DIR", str(_SHARED_EMBED_CACHE_DIR))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, str(_QA_DIR))
import scan_eval as se  # noqa: E402
from run_cv_index_baseline import CvIndexPredictor, _parse_build_summary  # noqa: E402


# --------------------------------------------------------------------------------------
# Чистая арифметика FPR/FNR — без torch/opencv, юнит-тестируется отдельно
# --------------------------------------------------------------------------------------


def compute_fnr(positive_scores: list[float], threshold: float) -> float:
    """Доля позитивных (вино РЕАЛЬНО есть в индексе) с top1_score < threshold — система
    ошибочно откатилась бы в not_in_catalog, хотя вино было бы найдено."""
    if not positive_scores:
        return 0.0
    return sum(1 for s in positive_scores if s < threshold) / len(positive_scores)


def compute_fpr(impostor_scores: list[float], threshold: float) -> float:
    """Доля импостеров (вина ТОЧНО нет в индексе) с top1_score >= threshold — система
    уверенно подсунула бы чужой slug вместо честного "не найдено"."""
    if not impostor_scores:
        return 0.0
    return sum(1 for s in impostor_scores if s >= threshold) / len(impostor_scores)


def sweep_thresholds(
    positive_scores: list[float],
    impostor_scores_by_source: dict[str, list[float]],
    thresholds: list[float],
) -> list[dict[str, Any]]:
    """Таблица FPR/FNR по сетке порогов. `impostor_scores_by_source` — {source_name: scores},
    отдельно на каждый источник плюс объединённый 'combined' (переиспользуют объединение
    всех непустых списков) — так расхождение между "held-out реальное вино" и "выдуманная
    этикетка" видно, не смешано в одно число молча."""
    combined = [s for scores in impostor_scores_by_source.values() for s in scores]
    rows = []
    for t in thresholds:
        row: dict[str, Any] = {
            "threshold": t,
            "fnr_positive": round(compute_fnr(positive_scores, t), 4),
            "fpr_combined": round(compute_fpr(combined, t), 4),
        }
        for source, scores in impostor_scores_by_source.items():
            row[f"fpr_{source}"] = round(compute_fpr(scores, t), 4)
        rows.append(row)
    return rows


def recommend_threshold(sweep_rows: list[dict[str, Any]], max_fpr: float = 0.05) -> dict[str, Any]:
    """Рекомендация, а не автоматический вывод: case.md называет ветку "нет в каталоге"
    ОСОБО высоко оцениваемой, а достоверность выдачи — самый тяжёлый критерий (50/100) —
    ложное уверенное "это вино X" по чужому/несуществующему вину бьёт по обоим сразу
    (неверная карточка ЗАСЧИТЫВАЕТСЯ как попытка, но неверная; честное "не найдено"
    засчитывается как корректная обработка ветки). Поэтому приоритет — низкий FPR, не
    равновесие FPR=FNR: ищем МИНИМАЛЬНЫЙ порог, при котором `fpr_combined <= max_fpr`
    (минимальный — чтобы не жертвовать FNR больше необходимого при заданном потолке FPR).
    Если даже порог 1.0 не даёт `fpr_combined <= max_fpr` — распределения не разделяются
    вовсе, возвращаем None: решение о рычаге (не порог, а фичи/модель) — не эта функция.
    Для контекста возвращает и точку равных ошибок (|FPR-FNR| минимальна) — нейтральный
    референс, не рекомендация."""
    candidates = [r for r in sweep_rows if r["fpr_combined"] <= max_fpr]
    recommended = min(candidates, key=lambda r: r["threshold"]) if candidates else None

    eer_row = min(sweep_rows, key=lambda r: abs(r["fpr_combined"] - r["fnr_positive"]))

    return {
        "max_fpr_target": max_fpr,
        "recommended": recommended,
        "recommended_rationale": (
            f"минимальный порог с fpr_combined <= {max_fpr} — приоритет ложноположительным "
            "(уверенно подставить чужой slug), т.к. case.md оценивает честный not_in_catalog "
            "особо высоко, а достоверность выдачи — самый тяжёлый критерий (50/100)"
            if recommended is not None
            else f"НИ ОДИН порог из сетки не даёт fpr_combined <= {max_fpr} — распределения "
            "позитива и импостеров не разделяются данным порогом достаточно резко; нужен "
            "другой рычаг (верификатор/домен), не просто сдвиг порога"
        ),
        "equal_error_rate_point": eer_row,
    }


def _parse_labels_csv(csv_path: Path) -> list[dict[str, str]]:
    import csv

    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_calibration_index() -> dict[str, Any]:
    if not _CV_CLI.is_file():
        raise RuntimeError(f"не найден CLI пакета cv: {_CV_CLI}")
    if not _REFS_CSV.is_file():
        raise RuntimeError(f"нет {_REFS_CSV} — сначала запустить qa/gen_impostor_photos.py")
    cmd = [
        str(_CV_CLI), "build-index",
        "--refs", str(_REFS_CSV),
        "--version", INDEX_VERSION,
        "--views", str(BUILD_VIEWS),
        "--seed", str(BUILD_SEED),
    ]
    print(f"[calibrate] сборка калибровочного индекса: {' '.join(cmd)}")
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(_CV_PKG_DIR), env=os.environ.copy(), capture_output=True, text=True)
    dt = time.perf_counter() - t0
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        raise RuntimeError(f"cv build-index (калибровка) упал (код {proc.returncode})")
    print(f"[calibrate] build-index занял {dt:.1f}с")
    summary = _parse_build_summary(proc.stdout, INDEX_VERSION)
    summary["wall_s"] = round(dt, 2)
    return summary


def main() -> int:
    build_summary = build_calibration_index()

    from cv.index import ImageIndex  # лениво

    index = ImageIndex()
    t_warm = time.perf_counter()
    _ = index.encoder.dim
    print(f"[calibrate] прогрев энкодера: {time.perf_counter() - t_warm:.1f}с")
    predictor = CvIndexPredictor(index)

    # --- позитив: пересчитан ПРОТИВ калибровочного индекса (не переиспользованы старые
    # скоры baseline — набор конкурентов в ANN другой, см. докстринг модуля) -------------
    positive_items, positive_warnings = se.load_eval_set(_POSITIVE_PHOTOS_DIR, _POSITIVE_LABELS_CSV)
    positive_records = se.run_eval(positive_items, predictor)
    positive_scores = [r.top1_score for r in positive_records if r.error is None and r.top1_score is not None]

    # --- импостеры, по источникам --------------------------------------------------------
    impostor_rows = _parse_labels_csv(_IMPOSTOR_LABELS_CSV)
    source_by_filename = {row["filename"]: row["source"] for row in impostor_rows}
    impostor_items, impostor_warnings = se.load_eval_set(_IMPOSTOR_PHOTOS_DIR, _IMPOSTOR_LABELS_CSV)
    impostor_records = se.run_eval(impostor_items, predictor)

    scores_by_source: dict[str, list[float]] = {}
    for rec in impostor_records:
        if rec.error is not None or rec.top1_score is None:
            continue
        source = source_by_filename.get(rec.photo_id, "unknown")
        scores_by_source.setdefault(source, []).append(rec.top1_score)

    sweep = sweep_thresholds(positive_scores, scores_by_source, THRESHOLD_GRID)
    recommendation = recommend_threshold(sweep, max_fpr=0.05)
    current_row = min(sweep, key=lambda r: abs(r["threshold"] - CURRENT_THRESHOLD))

    report = {
        "generator": "qa/calibrate_not_in_catalog_threshold.py",
        "generated_at": se.datetime.now(se.timezone.utc).isoformat(),
        "dataset_note": "синтетика (held-out реальные вина + выдуманные этикетки), не полевые фото кейсодержателя",
        "calibration_index": build_summary,
        "n_positive": len(positive_scores),
        "n_impostor_by_source": {k: len(v) for k, v in scores_by_source.items()},
        "current_threshold": CURRENT_THRESHOLD,
        "current_threshold_row": current_row,
        "recommendation": recommendation,
        "sweep": sweep,
        "warnings": [*positive_warnings, *impostor_warnings],
    }

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _OUT_DIR / "report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# Калибровка CV_CONFIDENT_SCORE_THRESHOLD — impostor-холдаут",
        "",
        f"Позитив: n={len(positive_scores)} (`qa/synthetic/photos`, пересчитан против "
        f"калибровочного индекса). Импостеры: {report['n_impostor_by_source']}.",
        "",
        f"Текущий порог `{CURRENT_THRESHOLD}` (apps/api/app/config.py::"
        f"cv_confident_score_threshold): FNR={current_row['fnr_positive']*100:.1f}%, "
        f"FPR_combined={current_row['fpr_combined']*100:.1f}%.",
        "",
        "## Сетка порогов (шаг 0.025)",
        "",
        "| threshold | FNR (позитив) | FPR combined | " + " | ".join(f"FPR {k}" for k in scores_by_source) + " |",
        "|---|---|---|" + "---|" * len(scores_by_source),
    ]
    for row in sweep:
        cells = [f"{row['threshold']:.3f}", f"{row['fnr_positive']*100:.1f}%", f"{row['fpr_combined']*100:.1f}%"]
        cells += [f"{row.get(f'fpr_{k}', 0)*100:.1f}%" for k in scores_by_source]
        md_lines.append("| " + " | ".join(cells) + " |")

    md_lines += ["", "## Рекомендация", ""]
    rec = recommendation["recommended"]
    if rec:
        md_lines.append(
            f"**Рекомендованный порог: {rec['threshold']}** — FNR={rec['fnr_positive']*100:.1f}%, "
            f"FPR_combined={rec['fpr_combined']*100:.1f}%. {recommendation['recommended_rationale']}."
        )
    else:
        md_lines.append(f"**Порог не подобран из сетки.** {recommendation['recommended_rationale']}.")
    eer = recommendation["equal_error_rate_point"]
    md_lines.append(
        f"\nСправочно — точка равных ошибок (EER, не рекомендация): threshold={eer['threshold']}, "
        f"FNR={eer['fnr_positive']*100:.1f}%, FPR_combined={eer['fpr_combined']*100:.1f}%."
    )
    if report["warnings"]:
        md_lines += ["", "## Предупреждения", ""] + [f"- {w}" for w in report["warnings"]]
    md_path = _OUT_DIR / "report.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[calibrate] отчёт -> {json_path}\n[calibrate]        -> {md_path}")
    print(
        f"[calibrate] текущий порог {CURRENT_THRESHOLD}: FNR={current_row['fnr_positive']*100:.1f}% "
        f"FPR_combined={current_row['fpr_combined']*100:.1f}%"
    )
    if rec:
        print(f"[calibrate] рекомендация: {rec['threshold']} (FNR={rec['fnr_positive']*100:.1f}%, FPR_combined={rec['fpr_combined']*100:.1f}%)")
    else:
        print(f"[calibrate] рекомендация: {recommendation['recommended_rationale']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
