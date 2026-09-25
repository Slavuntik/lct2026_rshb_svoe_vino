from shelf_api.text_check import TextCheck


def test_ocr_abstains_without_discriminating_words_and_rejects_readable_conflict():
    checker = TextCheck(
        {
            "port": {
                "name": "Массандра Портвейн белый Гурзуф",
                "brand": "Массандра",
                "grapes": ["Кокур"],
            },
            "muscat": {
                "name": "Массандра Мускат белый",
                "brand": "Массандра",
                "grapes": ["Мускат"],
            },
        },
        load_model=False,
    )
    assert checker.choose("МАССАНДРА", ["port", "muscat"]) is None
    assert checker.choose("", ["port", "muscat"]) is None
    assert checker.conflicts("МУСКАТ БЕЛЫЙ МАССАНДРА", "port")
    assert not checker.conflicts("", "port")
    assert not checker.conflicts("КАБЕРНЕ СОВИНЬОН", "unknown")


def test_blended_grapes_are_not_rejected_when_one_listed_grape_is_readable():
    checker = TextCheck(
        {"blend": {"name": "Красное вино", "grapes": ["Каберне Совиньон", "Мерло"]}},
        load_model=False,
    )
    assert not checker.conflicts("CABERNET SAUVIGNON", "blend")
    assert checker.conflicts("CHARDONNAY", "blend")


def test_rescue_requires_label_support_and_a_clear_geometric_winner():
    from shelf_api.policy import select_strong

    good = {
        "id": "a",
        "inliers": 32,
        "matches": 50,
        "labelInliers": 25,
        "coverage": 0.1,
    }
    assert select_strong([good]) == "a"
    assert select_strong([{**good, "labelInliers": 8}]) is None
    assert select_strong([good, {**good, "id": "b", "inliers": 30}]) is None
    assert select_strong([{**good, "matches": 100}]) is None


def test_category_and_grape_are_not_mutually_exclusive():
    checker = TextCheck(
        {"port": {"name": "Портвейн", "grapes": ["Мускат"]}}, load_model=False
    )
    assert not checker.conflicts("МУСКАТ", "port")


def test_disagreement_priority_uses_visual_leader_not_strong_unrelated_rival():
    from shelf_api.policy import disagreement_priority

    weak_leader = [
        {"id": "expected", "inliers": 8, "labelInliers": 5, "matches": 30},
        {"id": "rival", "inliers": 80, "labelInliers": 70, "matches": 90},
    ]
    useful_leader = [
        {"id": "expected", "inliers": 24, "labelInliers": 20, "matches": 40}
    ]
    assert disagreement_priority(["expected"], useful_leader) > disagreement_priority(
        ["expected"], weak_leader
    )
    assert disagreement_priority([], useful_leader) == (0, 0, 0)
    assert disagreement_priority(["missing"], useful_leader) == (0, 0, 0)


def test_rescue_preserves_local_competitors_outside_visual_top_three():
    from shelf_api.policy import rescue_candidates

    visual = ["leader", "variant-a", "variant-b", "fourth"]
    labels = ["different-brand", "variant-a", "similar-label", "fourth-local"]
    candidates = rescue_candidates(visual, labels)
    assert candidates[0] == "leader"
    assert "different-brand" in candidates and "similar-label" in candidates
    assert len(candidates) == len(set(candidates)) <= 6
    assert visual[3] == "fourth" and labels[3] == "fourth-local"
