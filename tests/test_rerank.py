from winescan.search.rerank import rerank
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


def test_zero_weight_keeps_visual_order():
    label = LabelText.from_ocr("Алиготе Баррель 2025")

    ranked = rerank(["aligote-barrel-2024", "aligote-barrel-2025"], [0.81, 0.80], CARDS, label, text_weight=0.0)

    assert ranked[0].slug == "aligote-barrel-2024"
