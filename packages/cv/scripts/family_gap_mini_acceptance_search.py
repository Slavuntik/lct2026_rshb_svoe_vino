"""G4 мини-приёмка (agents/G4-family-gap.md п.4), шаг 1 — короткая qdrant-сессия.

20 семейных кейсов из `case-data/families.json`: семьи с РАЗНЫМИ фото на участника
(>=2 проиндексированных участника, НЕ делящих один файл-эталон — тот же критерий,
что "141 семья с разными фото" в `scripts/near_dup_gap_report.py`, G3), выбранных
СЛУЧАЙНО (детерминированный seed) из всей квалифицирующейся популяции — брать первые
N по алфавиту оказалось смещённой выборкой: одна винодельня ("abrau-dyurso") занимает
начало алфавита и почти всегда несёт `differentiator: "прочее"`.

Для каждого кейса — один свежий holdout-ракурс ОДНОГО участника (детерминированно
первый индексированный слаг семьи) -> `ImageIndex.search()` на текущем боевом индексе
(`case-20260917`, НЕ пересобирается). Пишет промежуточный JSON для шага 2 (`family_gap_
mini_acceptance_verify.py` — OCR-верификатор, БЕЗ qdrant, без ограничения по времени —
разделение шагов буквально то, что просит ORCHESTRATION.md: держать qdrant-сессии
короткими).

Запуск (индекс case-20260917 уже собран):
    cd packages/cv && source .venv/bin/activate
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/family_gap_mini_acceptance_search.py
"""
from __future__ import annotations

import base64
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # cv устанавливаемый пакет, но на всякий случай

from cv import imageio
from cv.augment import render_synthetic_views
from cv.cli import discover_refs_from_slug_refs_json
from cv.index import ImageIndex

CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_DIR = CASE_DATA_DIR / "prod-svoe-vino-strapi" / "prod-svoe-vino" / "strapi" / "uploads"
HOLDOUT_SEED = 20260916  # тот же холдаут-seed, что near_dup_gap_report.py (G3) — сравнимость
SAMPLE_SEED = 20260917  # детерминированная случайная выборка 20 семей из всей популяции
N_CASES = 20
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "family_gap_mini_acceptance_search.json"


def _load_catalog_names() -> dict[str, str]:
    """Название кандидата — из дампа каталога (CSV "Название вина"), НЕ из
    `slug_refs.json["name"]` (то поле — побочный продукт сопоставления фото F3,
    часто короче/генеричнее настоящего каталожного имени: проверено на q2 —
    CSV несёт "Портвейн Белый Гурзуф", не просто "Портвейн"). Ближе к тому, что
    реально передаёт `apps/api` через `retriever.get_by_id()`/`source["name"]`
    (не моя зона — не видел код ingestion, но дамп каталога — источник истины по
    слагам согласно `contracts/image-scan.md`, "Данные кейса")."""
    out: dict[str, str] = {}
    with open(CASE_DATA_DIR / "strapi_output0709.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug, name = row.get("Slug"), row.get("Название вина")
            if slug and name and slug not in out:
                out[slug] = name
    return out


def main() -> None:
    refs_map = discover_refs_from_slug_refs_json(CASE_DATA_DIR / "slug_refs.json", UPLOADS_DIR)
    primary_paths = {slug: paths[0] for slug, paths in refs_map.items()}

    slug_refs_payload = json.loads((CASE_DATA_DIR / "slug_refs.json").read_text(encoding="utf-8"))
    slug_mapping = slug_refs_payload.get("mapping", slug_refs_payload)
    csv_names = _load_catalog_names()

    def candidate_name(slug: str) -> str:
        return csv_names.get(slug) or (slug_mapping.get(slug) or {}).get("name", slug)

    families_raw = json.loads((CASE_DATA_DIR / "families.json").read_text(encoding="utf-8"))

    qualifying_all = []
    for family_id, fam in sorted(families_raw.items()):
        members = fam.get("slugs", [])
        chosen_files = fam.get("chosen_files", {})
        indexed = [m for m in members if m in primary_paths]
        if len(indexed) < 2:
            continue
        if len({chosen_files.get(m) for m in indexed}) < 2:
            continue  # все делят один файл-эталон — это НЕ "с разными фото"
        qualifying_all.append((family_id, fam, indexed))

    print(f"[step1] всего семей с разными фото: {len(qualifying_all)}", file=sys.stderr)
    qualifying = random.Random(SAMPLE_SEED).sample(qualifying_all, N_CASES)
    print(f"[step1] случайная выборка: {N_CASES} семей (seed={SAMPLE_SEED})", file=sys.stderr)

    index = ImageIndex()
    if index.index_version != "case-20260917":
        raise SystemExit(f"ожидал готовый индекс case-20260917, нашёл {index.index_version!r} — не пересобираю")

    cases = []
    for family_id, fam, indexed in qualifying:
        target_slug = indexed[0]  # детерминированный выбор — первый индексированный член семьи
        arr = imageio.load_image_file(str(primary_paths[target_slug]))
        view = render_synthetic_views(arr, n=1, seed=HOLDOUT_SEED)[0]
        view_bytes = imageio.encode_jpeg(view)

        matches = index.search(view_bytes, top_k=50)
        top1 = matches[0] if matches else None
        by_slug = {m.slug: m for m in matches}
        family_candidates_in_topk = [
            {"slug": m.slug, "name": candidate_name(m.slug), "score": m.score}
            for m in matches
            if m.slug in indexed
        ]

        cases.append({
            "family_id": family_id,
            "differentiator": fam.get("differentiator"),
            "family_members_indexed": indexed,
            "target_slug": target_slug,
            "target_present_in_top50": target_slug in by_slug,
            "top1_slug": top1.slug if top1 else None,
            "top1_score": top1.score if top1 else None,
            "top1_gap": top1.gap if top1 else None,
            "top1_gap_is_number": (top1.gap is not None) if top1 else False,
            "cv_top1_correct": bool(top1 and top1.slug == target_slug),
            "family_candidates_in_top50": family_candidates_in_topk,
            # Байты ИМЕННО этого кадра (base64) — шаг 2 (verify(), без qdrant) гоняет
            # OCR на том же запросе, не порождает его заново.
            "view_bytes_b64": base64.b64encode(view_bytes).decode("ascii"),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")

    n_gap_number = sum(1 for c in cases if c["top1_gap_is_number"])
    print(f"[step1] family-gap != null: {n_gap_number}/{len(cases)}", file=sys.stderr)
    print(
        f"[step1] cv top1 уже верный (нечего переставлять): "
        f"{sum(1 for c in cases if c['cv_top1_correct'])}/{len(cases)}",
        file=sys.stderr,
    )
    print(f"[step1] -> {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
