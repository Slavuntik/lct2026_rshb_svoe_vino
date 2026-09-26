from PIL import Image
from audit_vlm_references import candidate_order, convert_response, reference_strip


def test_reference_order_does_not_reveal_retrieval_rank():
    items = ["a", "b", "c", "d", "e"]
    assert candidate_order(items, "photo", 1) == candidate_order(
        list(reversed(items)), "photo", 1
    )
    assert set(candidate_order(items, "photo", 1)) == set(items)


def test_slots_are_scoped_to_query_and_need_discriminating_evidence():
    slots = {1: {"A": "first"}, 2: {"A": "second"}}
    response = {
        "bottles": [
            {"crop_id": 1, "selected_slot": "A", "difference": "distinct label"},
            {"crop_id": 2, "selected_slot": "A", "difference": "different text"},
            {"crop_id": 3, "selected_slot": "A", "difference": "unsupported crop"},
            {"crop_id": 1, "selected_slot": "B", "difference": "invented slot"},
            {"crop_id": 2, "selected_slot": "A", "difference": ""},
        ]
    }
    assert [b["selected_id"] for b in convert_response(response, slots)] == [
        "first",
        "second",
        None,
        None,
    ]


def test_reference_sheet_accepts_missing_candidates_and_small_crops():
    query = Image.new("RGB", (5, 30), "red")
    assert reference_strip(query, [], 1).size == (192, 500)
    assert reference_strip(query, [query] * 5, 2).size == (1152, 500)
