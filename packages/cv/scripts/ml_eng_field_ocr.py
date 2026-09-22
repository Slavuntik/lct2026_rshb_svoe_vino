#!/usr/bin/env python3
"""packages/cv/scripts/ml_eng_field_ocr.py — ml-engineer, бриф тимлида 22.09
("index-20-views"): СВЕЖИЙ локальный RapidOCR-текст (`cv.verify.LabelVerifier(
engine="rapid", rapid_sizes=(640,960)).read_query_text()` — тот же боевой вызов,
что `qa/real_photos_choose_rule.py::regen_live_ocr()` использовал для 100 живых
фото, features/ocr_live_verifier.jsonl) на ДВУХ наборах, для которых такого
кэша ещё нет в `case-data/real-photos-labels/features/`:

  - 32 полевых кадра (part3-field.csv) — исходники в `/Users/vyacheslavfokin/
    ClaudeWorkspace/vines/Field/`.
  - 85 кропов бутылок с тех же полевых снимков (part4-field-bottles.csv) —
    исходники в `case-data/real-photos-labels/field-bottles/`.

ЦЕЛИКОМ офлайн: RapidOCR — ONNX Runtime, CPU, без сети (см. docs/architecture/
models-and-algorithms.md §1.2). НЕ трогает GPU-шлюз/VLM (бриф запрещает) — эти
JSONL несут только OCR-текст, "model"/"merge" ветки слияния на этих двух наборах
поэтому не считаются (нет VLM-текста), только "CV-only" и "local" (CV+OCR).

Вывод — `case-data/ml-eng-index-20v/features/ocr_{field_frames,field_bottles}_live.jsonl`
(вне git, `case-data/` целиком в .gitignore), формат идентичен `ocr_live_verifier.jsonl`
({"photo": ..., "text": ...} построчно) — та же схема, что `qa/real_photos_choose_rule.py::
load_jsonl_text()` читает.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CV_PKG_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_CV_PKG_DIR))

LABELS_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels")
FIELD_FRAMES_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/Field")
FIELD_BOTTLES_DIR = LABELS_DIR / "field-bottles"
OUT_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/ml-eng-index-20v/features")

SETS = {
    "field_frames": (LABELS_DIR / "part3-field.csv", FIELD_FRAMES_DIR, "photo"),
    "field_bottles": (LABELS_DIR / "part4-field-bottles.csv", FIELD_BOTTLES_DIR, "photo"),
}


def run_set(tag: str, csv_path: Path, images_dir: Path, photo_col: str) -> None:
    from cv.verify import LabelVerifier

    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    photos = [r[photo_col] for r in rows]
    missing = [p for p in photos if not (images_dir / p).is_file()]
    assert not missing, f"{tag}: отсутствуют на диске: {missing}"

    v = LabelVerifier(engine="rapid", rapid_sizes=(640, 960))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"ocr_{tag}_live.jsonl"
    t0 = time.perf_counter()
    with out_path.open("w", encoding="utf-8") as out:
        for i, name in enumerate(photos, 1):
            data = (images_dir / name).read_bytes()
            text = v.read_query_text(data)
            out.write(json.dumps({"photo": name, "text": text}, ensure_ascii=False) + "\n")
            out.flush()
            if i % 10 == 0 or i == len(photos):
                print(f"[ml-eng-field-ocr] {tag}: {i}/{len(photos)} за {time.perf_counter()-t0:.1f}с", file=sys.stderr)
    print(f"[ml-eng-field-ocr] готово: {out_path} ({len(photos)} фото)")


def main() -> int:
    for tag, (csv_path, images_dir, photo_col) in SETS.items():
        run_set(tag, csv_path, images_dir, photo_col)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
