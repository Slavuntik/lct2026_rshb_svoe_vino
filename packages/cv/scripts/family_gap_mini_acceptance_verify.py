"""G4 мини-приёмка (agents/G4-family-gap.md п.4), шаг 2 — БЕЗ qdrant, время не
ограничено (OCR — не индекс, лок embedded-qdrant не участвует).

Прогоняет `LabelVerifier.verify()` (с правкой типа/цвета из этой волны) на всех
кейсах шага 1 (`scripts/family_gap_mini_acceptance_search.py`), используя РЕАЛЬНЫХ
кандидатов семьи, найденных CV в топ-50, с именами из дампа каталога. Считает долю
family-gap != null и долю верных перестановок среди кейсов, где CV top-1 был неверен.

Запуск (после шага 1):
    cd packages/cv && source .venv/bin/activate
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 scripts/family_gap_mini_acceptance_verify.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv.verify import LabelVerifier

IN_PATH = Path(__file__).resolve().parent.parent / "data" / "family_gap_mini_acceptance_search.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "family_gap_mini_acceptance_final.json"


def main() -> None:
    cases = json.loads(IN_PATH.read_text(encoding="utf-8"))
    verifier = LabelVerifier()

    results = []
    for c in cases:
        view_bytes = base64.b64decode(c["view_bytes_b64"])
        candidates = [
            {"slug": fc["slug"], "name": fc["name"], "vintage": None}
            for fc in c["family_candidates_in_top50"]
        ]
        # verify() c <=1 кандидатом не несёт решения по построению (нечего сравнивать) —
        # не тратим OCR-вызов, тот же короткий путь, что и в LabelVerifier.verify()
        # на пустом списке кандидатов.
        decision = verifier.verify(view_bytes, candidates) if len(candidates) > 1 else None
        results.append({
            "family_id": c["family_id"],
            "differentiator": c["differentiator"],
            "target_slug": c["target_slug"],
            "cv_top1_correct": c["cv_top1_correct"],
            "top1_gap_is_number": c["top1_gap_is_number"],
            "n_family_candidates": len(candidates),
            "verify_decision": decision,
            "verify_correct": decision == c["target_slug"],
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(results)
    n_gap_number = sum(1 for r in results if r["top1_gap_is_number"])
    needs_permutation = [r for r in results if not r["cv_top1_correct"]]
    n_needs = len(needs_permutation)
    n_fixed = sum(1 for r in needs_permutation if r["verify_correct"])
    n_abstained = sum(1 for r in needs_permutation if r["verify_decision"] is None)
    n_wrong_pick = sum(1 for r in needs_permutation if r["verify_decision"] is not None and not r["verify_correct"])
    already_correct = [r for r in results if r["cv_top1_correct"]]
    n_broken = sum(1 for r in already_correct if r["verify_decision"] not in (None, r["target_slug"]))

    print(f"[step2] family-gap != null: {n_gap_number}/{n}", file=sys.stderr)
    print(f"[step2] cv top1 уже верный: {n - n_needs}/{n}", file=sys.stderr)
    print(f"[step2] нужна перестановка (cv top1 неверный): {n_needs}/{n}", file=sys.stderr)
    print(f"[step2]   верификатор поставил ВЕРНЫЙ слаг: {n_fixed}/{n_needs}", file=sys.stderr)
    print(f"[step2]   верификатор воздержался (None): {n_abstained}/{n_needs}", file=sys.stderr)
    print(f"[step2]   верификатор поставил ДРУГОЙ неверный слаг: {n_wrong_pick}/{n_needs}", file=sys.stderr)
    print(
        f"[step2] уже верные top-1, которые верификатор НЕ сломал: "
        f"{len(already_correct) - n_broken}/{len(already_correct)}",
        file=sys.stderr,
    )
    print(f"[step2] -> {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
