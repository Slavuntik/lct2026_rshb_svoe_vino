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
