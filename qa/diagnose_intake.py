#!/usr/bin/env python3
"""qa/diagnose_intake.py — диагностика приёма фото для «дня датасета» (агент F2, поручение
оркестратора после ревью 04; см. qa/acceptance.md, «Порядок дня датасета», пункт (г)).

Три диагностических числа, которые ревью 04 требует посчитать ДО любого тюнинга модельных
ручек ("эти три числа решают, куда идёт следующий день работы — без них любой тюнинг
вслепую", ревью 04):
  1. **label-detector fallback-rate** — доля фото, на которых `cv.normalize._foreground_bbox()`
     не смог оценить силуэт бутылки, и `detect_label_region()` откатилась на грубый
     центральный кроп (типовые причины по ревью 04: пёстрая рамка кадра, MAD>30 — типовая
     "полка"; клоузап, area>0.97 — типовой крупный план).
  2. **EXIF-rotation share** — доля фото с EXIF Orientation != 1 — блокер 2 ревью 04:
     `packages/cv/cv/imageio.py::decode_image()` пока НЕ применяет EXIF Orientation, поэтому
     повёрнутые телефонные фото сегодня декодируются боком без предупреждения.
  3. **Классификация ошибок top-1 по типам** — near_dup_family / detector_fallback /
     not_in_catalog / other — поверх уже посчитанного отчёта `scan_eval.py`/
     `run_cv_index_baseline.py` (report.json), скрещенная с диагностикой (1).

На СИНТЕТИКЕ (фото без камеры — сгенерированы программно) числа (2) тривиальны (0%) — это
ОЖИДАЕМО, а не находка: инструмент — скелет, готовый принять `--photos-dir` публичного
датасета кейса в день его приезда без единой правки кода.

Запускать через venv пакета cv (нужны cv2/numpy/PIL из packages/cv/.venv):
    packages/cv/.venv/bin/python qa/diagnose_intake.py \\
        --photos-dir qa/synthetic/photos --labels-csv qa/synthetic/labels.csv \\
        --scan-eval-report qa/scan-eval-runs/synthetic-cv-index-baseline/report.json

Чистая логика (classify_error/summarize_error_types/is_same_near_dup_family) — без cv2/PIL,
тестируется под обычным qa/.venv (qa/test_diagnose_intake.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_QA_DIR = Path(__file__).resolve().parent

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EXIF_ORIENTATION_TAG = 274

# Известные near-dup семьи — зеркало packages/cv/cv/selfcheck.py::NEAR_DUP_GROUPS (READ-ONLY
# ссылка, не импорт: тот список — про devfix дев-фикстуры, этот — про eval-сет ЭТОГО
# инструмента; в день датасета сюда подставляется полная опись семей из дампа кейса, ревью
# 04 чек-лист п.2, а не эта заглушка на одну пару).
KNOWN_NEAR_DUP_GROUPS: list[frozenset[str]] = [
    frozenset({"aligote-barrel-2024", "aligote-barrel-2025"}),
]


# --------------------------------------------------------------------------------------
# Чистая логика — без cv2/PIL/numpy
# --------------------------------------------------------------------------------------


def is_same_near_dup_family(slug_a: str, slug_b: str, groups: list[frozenset[str]]) -> bool:
    return any(slug_a in g and slug_b in g for g in groups)


def classify_error(
    true_slug: str,
    predicted_slug: str | None,
    *,
    near_dup_groups: list[frozenset[str]],
    query_fallback: bool,
) -> str:
    """Один из 'not_in_catalog' / 'near_dup_family' / 'detector_fallback' / 'other'.

    Порядок проверок — от наиболее объяснимой причины: near-dup семья объясняет промах
    структурно (case.md: near-duplicates — "главный источник ошибок") ДАЖЕ если запрос
    заодно прошёл через fallback-кроп — эмбеддинги двух почти идентичных этикеток
    неотличимы независимо от качества кропа (cv/selfcheck.py). Поэтому near-dup
    проверяется ПЕРВЫМ, `detector_fallback` — только если near-dup не объясняет промах."""
    if predicted_slug is None:
        return "not_in_catalog"
    if is_same_near_dup_family(true_slug, predicted_slug, near_dup_groups):
        return "near_dup_family"
    if query_fallback:
        return "detector_fallback"
    return "other"


def summarize_error_types(classified: list[str]) -> dict[str, Any]:
    """{type: count} + доля от числа ОШИБОК (вызывается только на промахах, не на всём
    eval-сете) — процент от общего eval-сета считает вызывающая сторона отдельно, если
    нужен (нужен n_total, которого здесь нет)."""
    total = len(classified)
    counts: dict[str, int] = {}
    for c in classified:
        counts[c] = counts.get(c, 0) + 1
    return {
        "n_errors": total,
        "by_type": counts,
        "by_type_pct": {k: round(v / total * 100, 1) for k, v in counts.items()} if total else {},
    }


def discover_photo_files(photos_dir: Path) -> list[Path]:
    return sorted(p for p in photos_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


# --------------------------------------------------------------------------------------
# Диагностики, требующие PIL/cv2/numpy — лениво внутри функций (модуль остаётся
# импортируемым и тестируемым под qa/.venv без этих зависимостей)
# --------------------------------------------------------------------------------------


def exif_orientation_tag(path: Path) -> int | None:
    """Значение тега EXIF Orientation (274) исходного файла; `None`, если тега/EXIF нет
    вовсе. 1 = "нормально"; 3/6/8 — типовые повороты 180°/270°/90° телефонных камер."""
    from PIL import Image

    with Image.open(path) as im:
        exif = im.getexif()
    return exif.get(EXIF_ORIENTATION_TAG) if exif else None


def is_label_detector_fallback(image_path: Path) -> bool:
    """True, если `cv.normalize._foreground_bbox()` не смог оценить силуэт бутылки —
    `detect_label_region()` тогда откатывается на грубый центральный кроп (ревью 04,
    раздел "Разрыв 93.9% vs 75.6%"). READ-ONLY использование функции с ведущим
    подчёркиванием (соглашение об именах пакета cv, не техническое ограничение доступа) —
    это измерение, не правка cv."""
    from cv import imageio
    from cv.normalize import _foreground_bbox

    arr = imageio.load_image_file(str(image_path))
    return _foreground_bbox(arr) is None


def compute_intake_diagnostics(photo_paths: list[Path]) -> dict[str, Any]:
    """Числа (1) и (2) по каталогу фото, один проход на файл. `per_file` — для скрещивания
    с классификацией ошибок (3) вызывающей стороной."""
    per_file: dict[str, dict[str, Any]] = {}
    n_fallback = 0
    n_exif_present = 0
    n_exif_rotated = 0
    n_unreadable = 0
    for path in photo_paths:
        try:
            fallback = is_label_detector_fallback(path)
        except ValueError:
            fallback = None
            n_unreadable += 1
        orientation = exif_orientation_tag(path)
        rotated = orientation is not None and orientation != 1
        per_file[path.name] = {
            "detector_fallback": fallback,
            "exif_orientation": orientation,
            "exif_rotated": rotated,
        }
        if fallback:
            n_fallback += 1
        if orientation is not None:
            n_exif_present += 1
        if rotated:
            n_exif_rotated += 1

    n = len(photo_paths)
    return {
        "n_photos": n,
        "n_unreadable": n_unreadable,
        "fallback_rate": round(n_fallback / n, 4) if n else 0.0,
        "exif_present_rate": round(n_exif_present / n, 4) if n else 0.0,
        "exif_rotated_rate": round(n_exif_rotated / n, 4) if n else 0.0,
        "per_file": per_file,
    }


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Диагностика приёма фото — день датасета (ревью 04)")
    parser.add_argument("--photos-dir", required=True, type=Path)
    parser.add_argument(
        "--scan-eval-report", type=Path, default=None,
        help="report.json от scan_eval.py/run_cv_index_baseline.py — для классификации "
        "ошибок (3); без него считаются только диагностики (1)/(2)",
    )
    parser.add_argument("--out-dir", type=Path, default=_QA_DIR / "scan-eval-runs" / "intake-diagnostics")
    return parser


def run(argv: list[str]) -> int:
    args = _build_arg_parser().parse_args(argv)
    if not args.photos_dir.is_dir():
        print(f"[diagnose-intake] каталог не найден: {args.photos_dir}", file=sys.stderr)
        return 2

    photo_paths = discover_photo_files(args.photos_dir)
    if not photo_paths:
        print(f"[diagnose-intake] нет фото в {args.photos_dir}", file=sys.stderr)
        return 2

    diagnostics = compute_intake_diagnostics(photo_paths)

    error_summary = None
    if args.scan_eval_report and args.scan_eval_report.is_file():
        report = json.loads(args.scan_eval_report.read_text(encoding="utf-8"))
        classified = []
        for rec in report.get("records", []):
            if rec.get("error") is not None:
                continue
            true_slug = rec.get("true_slug")
            top1_slug = rec.get("top1_slug")
            if top1_slug == true_slug:
                continue
            fallback_info = diagnostics["per_file"].get(rec.get("photo_id"), {})
            classified.append(
                classify_error(
                    true_slug, top1_slug,
                    near_dup_groups=KNOWN_NEAR_DUP_GROUPS,
                    query_fallback=bool(fallback_info.get("detector_fallback")),
                )
            )
        error_summary = summarize_error_types(classified)

    out = {
        "generator": "qa/diagnose_intake.py",
        "photos_dir": str(args.photos_dir),
        "diagnostics": {k: v for k, v in diagnostics.items() if k != "per_file"},
        "error_classification": error_summary,
        "note": (
            "На синтетике (программно сгенерированные фото) EXIF/поворот тривиальны (0%) "
            "— ОЖИДАЕМО, не находка. Инструмент — скелет для дня датасета: "
            "--photos-dir <публичный набор кейса> без единой правки кода."
        ),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "report.json").write_text(
        json.dumps({**out, "per_file": diagnostics["per_file"]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[diagnose-intake] отчёт -> {args.out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
