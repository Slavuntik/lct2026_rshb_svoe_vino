"""Gap-статистика near-dup семей каталога кейса (G3, agents/G3-real-index.md п.5).

Для каждой семьи из `case-data/families.json` (F3, `qa/case_census.py` — отдельный
файл, переехал со встроенного `slug_refs.json["families"]` 16.09.2026, прямо в
процессе этой волны; `chosen_files` внутри семьи говорит, делят ли участники ОДИН
файл-эталон):
- если делят — их "real"-векторы в индексе ЧИСЛЕННО идентичны (тот же исходный файл
  -> тот же `normalize_query()` -> тот же эмбеддинг), так что для любого запроса их
  score совпадает БИТ В БИТ — gap≈0 не приближение, а структурное свойство: CV
  принципиально не может их различить, разделение — работа OCR-верификатора
  (contracts/image-scan.md, "Пайплайн /scan/photo"; cv/verify.py — не эта волна);
- для семей с РАЗНЫМИ фото на участника — реальный `index.search()` на свежем
  holdout-ракурсе (другой seed, чем build) каждого проиндексированного участника даёт
  распределение gap/score-разницы с сиблингами — есть ли у CV вообще сигнал.

Дополнение оркестратора (16.09.2026, п.3): каждая строка несёт `fallback_ref` —
триггерил ли ЭТАЛОН этого участника fallback детектора этикетки (cv.audit) — шумный
кроп может исказить gap независимо от реальной похожести контента, разбираем отдельно.

`families.json` также несёт СОБСТВЕННЫЙ OCR-вердикт F3 (`ocr.verdict`: виден ли год
печатным текстом на эталоне) — включаем его в строку как независимую сверку: семья,
где CV не видит сигнала (score-разница ~0), но F3 отмечает год НЕвидимым на фото —
ожидаемо неразличима вообще ничем, кроме доп. данных (объём/наклейка/штрихкод), не
только OCR-верификатором.

Не CLI-подкоманда `cv` — разовый отчёт под КОНКРЕТНУЮ структуру датасета кейса, не
переиспользуемая операция индекса; тот же принцип, что `scripts/fetch_devfix.py`
(агент G) — отдельный скрипт, использующий пакет `cv`, а не часть его контракта.

Запуск (индекс версии case-20260916 уже собран):
    cd packages/cv && source .venv/bin/activate
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/near_dup_gap_report.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # cv устанавливаемый пакет, но на всякий случай

from cv import imageio
from cv.audit import label_detector_outcomes
from cv.augment import render_synthetic_views
from cv.cli import discover_refs_from_slug_refs_json
from cv.index import ImageIndex

CASE_DATA_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UPLOADS_DIR = CASE_DATA_DIR / "prod-svoe-vino-strapi" / "prod-svoe-vino" / "strapi" / "uploads"
HOLDOUT_SEED = 20260916  # отличен от build-seed (0, config.AUGMENT_SEED_DEFAULT) -> свежий ракурс
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "near_dup_gap_report.json"


def main() -> None:
    refs_map = discover_refs_from_slug_refs_json(CASE_DATA_DIR / "slug_refs.json", UPLOADS_DIR)
    primary_paths = {slug: paths[0] for slug, paths in refs_map.items()}

    families_raw = json.loads((CASE_DATA_DIR / "families.json").read_text(encoding="utf-8"))

    family_member_slugs = sorted({s for fam in families_raw.values() for s in fam.get("slugs", [])})
    family_primary_paths = {s: primary_paths[s] for s in family_member_slugs if s in primary_paths}
    audit = label_detector_outcomes(family_primary_paths, verbose=False)
    fallback_set = set(audit["fallback_slugs"])

    index = ImageIndex()
    if not index.index_version:
        raise SystemExit("Индекс не построен (manifest отсутствует) — сначала cv build-index")

    families_out = []
    for family_id, fam in sorted(families_raw.items()):
        members = fam.get("slugs", [])
        chosen_files = fam.get("chosen_files", {})
        indexed_members = [m for m in members if m in primary_paths]

        pairs_share_photo = [
            (a, b)
            for i, a in enumerate(indexed_members)
            for b in indexed_members[i + 1 :]
            if chosen_files.get(a) and chosen_files.get(a) == chosen_files.get(b)
        ]

        rows = []
        for slug in indexed_members:
            arr = imageio.load_image_file(str(primary_paths[slug]))
            view = render_synthetic_views(arr, n=1, seed=HOLDOUT_SEED)[0]
            matches = index.search(imageio.encode_jpeg(view), top_k=50)
            by_slug = {m.slug: m for m in matches}
            top1 = matches[0] if matches else None
            own = by_slug.get(slug)
            sibling_scores = {sib: by_slug[sib].score for sib in indexed_members if sib != slug and sib in by_slug}
            max_abs_diff = max((abs(own.score - s) for s in sibling_scores.values()), default=None) if own else None
            rows.append(
                {
                    "slug": slug,
                    "fallback_ref": slug in fallback_set,
                    "top1_slug": top1.slug if top1 else None,
                    "top1_score": top1.score if top1 else None,
                    "top1_gap": top1.gap if top1 else None,
                    "self_is_top1": bool(top1 and top1.slug == slug),
                    "own_score": own.score if own else None,
                    "sibling_scores": sibling_scores,
                    "max_abs_score_diff_vs_sibling": max_abs_diff,
                }
            )

        families_out.append(
            {
                "family": family_id,
                "differentiator": fam.get("differentiator"),
                "f3_ocr_verdict": (fam.get("ocr") or {}).get("verdict"),
                "members_total": len(members),
                "members_indexed": len(indexed_members),
                "members_not_indexed": sorted(set(members) - set(indexed_members)),
                "shares_exact_photo": bool(pairs_share_photo),
                "shared_photo_pairs": pairs_share_photo,
                "rows": rows,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(families_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(families_out, ensure_ascii=False, indent=2))
    print(f"\n-> {OUT_PATH}")


if __name__ == "__main__":
    main()
