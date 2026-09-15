from winescan.search.rerank import Candidate, decide, fuse, rerank
from winescan.search.text_match import LabelText

CARDS = {
    "aligote-barrel-2024": {"name": "Алиготе Баррель, 2024", "winery": "Коммуналка", "grapes": ["Алиготе"],
                            "attributes": {"year": 2024, "sweetness": None}},
    "aligote-barrel-2025": {"name": "Алиготе Баррель, 2025", "winery": "Коммуналка", "grapes": ["Алиготе"],
                            "attributes": {"year": 2025, "sweetness": None}},
}  # fmt: skip


def test_text_breaks_visual_tie_between_vintages():
    label = LabelText.from_ocr("КОММУНАЛКА Алиготе Баррель 2025")

    ranked = rerank(["aligote-barrel-2024", "aligote-barrel-2025"], [0.81, 0.80], CARDS, label, text_weight=0.1)

    assert [c.slug for c in ranked] == ["aligote-barrel-2025", "aligote-barrel-2024"]


def test_fuse_local_inliers_reorder_near_duplicates():
    ranked = fuse(["a", "b"], [0.90, 0.89], local_inliers={"a": 5, "b": 120}, local_weight=0.05)

    assert [c.slug for c in ranked] == ["b", "a"]
    assert ranked[0].visual_score == 0.89 and ranked[0].local_inliers == 120


def test_decide_thresholds():
    candidates = [Candidate("a", 0.80, 0.70), Candidate("b", 0.79, 0.69)]

    assert decide(candidates).status == "found"
    assert decide(candidates, min_visual_score=0.75).status == "not_found"
    assert decide(candidates, min_margin=0.02).status == "not_found"
    assert decide([]).status == "not_found"


def test_zero_weight_keeps_visual_order():
    label = LabelText.from_ocr("Алиготе Баррель 2025")

    ranked = rerank(["aligote-barrel-2024", "aligote-barrel-2025"], [0.81, 0.80], CARDS, label, text_weight=0.0)

    assert ranked[0].slug == "aligote-barrel-2024"
