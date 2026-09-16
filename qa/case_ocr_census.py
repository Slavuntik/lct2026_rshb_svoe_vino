#!/usr/bin/env python3
"""qa/case_ocr_census.py — OCR-читаемость года на эталонных фото near-dup семей
(agents/F3-census.md, задача 3): "для всех слагов из семей прогони PaddleOCR по
эталонному фото... у каких семей год реально читается с эталона, у каких нет — это
решает, где верификатор вообще может помочь."

Пайплайн — ТОТ ЖЕ, что настоящий OCR-верификатор near-dup (`packages/cv/cv/verify.py`,
contracts/image-scan.md v0.4.4): `cv.normalize.normalize_query()` (детект области этикетки
→ кроп → развёртка цилиндра → фотометрия — та же нормализация, что видит `ImageIndex.
search()`) → `LabelVerifier.read_text()` (PaddleOCR) → `cv.verify._extract_years()` (год —
ровно 4 цифры 19xx/20xx). Год-регекс и OCR-пайплайн ИМПОРТИРУЮТСЯ, не копируются — не
разъедутся, если G поправит верификатор.

Запускать ТОЛЬКО через venv пакета cv (torch/paddleocr/cv2):

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
    packages/cv/.venv/bin/python qa/case_ocr_census.py \\
        --case-data-dir /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data

Читает case-data/families.json (qa/case_census.py) + case-data/slug_refs.json (chosen-файл
на слаг). Дописывает В families.json (тот же файл — не плодит третий артефакт): поле
`ocr` на каждую семью с per-член результатом (chosen/ocr_years/slug_year/readable/
matches_slug_year) и вердиктом семьи. mapping/differentiator/chosen_files — не трогает.

ВАЖНО: этот прогон измеряет читаемость года НА ЭТАЛОНЕ (студийное фото каталога), НЕ на
полевом фото пользователя (которого ещё нет) — так и назван в отчёте. Читаемость на
эталоне — верхняя граница: если год не читается даже на чистом студийном кадре, на
шумном полевом фото у телефона шансов ещё меньше."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_QA_DIR = Path(__file__).resolve().parent
DEFAULT_CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_SUBPATH = Path("prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads")

_SLUG_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case-data-dir", type=Path, default=DEFAULT_CASE_DATA_DIR)
    parser.add_argument("--limit", type=int, default=None, help="ограничить число семей (smoke-прогон)")
    args = parser.parse_args(argv)

    case_dir: Path = args.case_data_dir
    uploads_dir = case_dir / UPLOADS_SUBPATH
    families_path = case_dir / "families.json"
    slug_refs_path = case_dir / "slug_refs.json"

    families: dict[str, Any] = json.loads(families_path.read_text(encoding="utf-8"))
    slug_refs = json.loads(slug_refs_path.read_text(encoding="utf-8"))
    mapping = slug_refs["mapping"]

    from cv import imageio
    from cv.normalize import normalize_query
    from cv.verify import LabelVerifier, _extract_years  # тот же regex/пайплайн, что верификатор

    verifier = LabelVerifier()
    cache: dict[str, set[int]] = {}
    decode_errors: list[str] = []

    def ocr_years_for_file(fn: str) -> set[int]:
        if fn in cache:
            return cache[fn]
        path = uploads_dir / fn
        try:
            arr = imageio.decode_image(path.read_bytes())
            normalized = normalize_query(arr, enabled=True)
            text = verifier.read_text(normalized)
            years = _extract_years(text)
        except Exception as exc:  # noqa: BLE001 — единичный битый файл не должен ронять прогон
            years = set()
            decode_errors.append(fn)
            print(f"[case_ocr_census] WARN OCR failed on {fn}: {exc}", file=sys.stderr)
        cache[fn] = years
        return years

    family_ids = list(families)
    if args.limit:
        family_ids = family_ids[: args.limit]

    t0 = time.perf_counter()
    n_members = 0
    n_readable = 0
    n_families_with_readable = 0
    for idx, family_id in enumerate(family_ids):
        fam = families[family_id]
        member_results: dict[str, Any] = {}
        readable_members = 0
        for slug in fam["slugs"]:
            entry = mapping.get(slug, {})
            chosen = entry.get("chosen")
            m = _SLUG_YEAR_RE.search(slug)
            slug_year_int = int(m.group(0)) if m else None
            if not chosen:
                member_results[slug] = {
                    "chosen": None, "ocr_years": [], "slug_year": slug_year_int,
                    "readable": False, "matches_slug_year": False,
                }
                continue
            years = ocr_years_for_file(chosen)
            readable = bool(years)
            matches = slug_year_int is not None and slug_year_int in years
            if readable:
                readable_members += 1
                n_readable += 1
            n_members += 1
            member_results[slug] = {
                "chosen": chosen,
                "ocr_years": sorted(years),
                "slug_year": slug_year_int,
                "readable": readable,
                "matches_slug_year": matches,
            }
        if readable_members > 0:
            n_families_with_readable += 1
        fam["ocr"] = {
            "members": member_results,
            "readable_members": readable_members,
            "total_members": len(fam["slugs"]),
            "verdict": "год-виден-на-эталоне" if readable_members > 0 else "год-не-виден-на-эталоне",
        }
        if (idx + 1) % 50 == 0:
            elapsed = time.perf_counter() - t0
            print(f"[case_ocr_census] {idx+1}/{len(family_ids)} семей, {elapsed:.1f}с", file=sys.stderr)

    elapsed = time.perf_counter() - t0
    families_path.write_text(json.dumps(families, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "families_checked": len(family_ids),
        "families_with_readable_year": n_families_with_readable,
        "families_without_readable_year": len(family_ids) - n_families_with_readable,
        "members_checked": n_members,
        "members_year_readable": n_readable,
        "decode_errors": len(decode_errors),
        "elapsed_s": round(elapsed, 1),
        "note": "читаемость на ЭТАЛОНЕ каталога (студийное фото), не на полевом фото пользователя",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
