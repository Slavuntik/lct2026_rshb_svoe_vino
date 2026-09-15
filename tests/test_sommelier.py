import pytest

from winescan.product.sommelier import Sommelier, SommelierRequest, questions


def _card(slug, winery, category, grapes, sweetness="dry", sparkling=False, description="", region="Кубань"):
    return {"slug": slug, "name": slug, "winery": winery, "category": category, "grapes": list(grapes),
            "description": description, "region": region,
            "attributes": {"sweetness": sweetness, "sparkling": sparkling}}  # fmt: skip


CARDS = {c["slug"]: c for c in [
    _card("saperavi", "A", "Красное", ["Саперави"], description="выдержка в дубовых бочках, танины"),
    _card("pinot", "B", "Красное", ["Пино Нуар"], description="лёгкое, ягоды"),
    _card("riesling", "C", "Белое", ["Рислинг"]),
    _card("muscat-sweet", "D", "Белое", ["Мускат"], sweetness="sweet"),
    _card("brut", "E", "Белое", ["Шардоне"], sweetness="brut", sparkling=True),
    _card("riesling-2", "C", "Белое", ["Рислинг"], region="Крым"),
]}  # fmt: skip


def test_meat_gets_full_red():
    suggestions = Sommelier(CARDS).suggest(SommelierRequest(dish="meat", body="full"))

    assert suggestions[0].slug == "saperavi"
    assert "к блюду «мясо и стейки» подходят красные сухие вина" in suggestions[0].reasons


def test_fish_prefers_dry_white_and_one_wine_per_winery():
    slugs = [s.slug for s in Sommelier(CARDS).suggest(SommelierRequest(dish="fish"), limit=3)]

    assert slugs[0] == "riesling" and "riesling-2" not in slugs


def test_dessert_and_celebration():
    sommelier = Sommelier(CARDS)

    assert sommelier.suggest(SommelierRequest(dish="dessert"))[0].slug == "muscat-sweet"
    assert sommelier.suggest(SommelierRequest(dish="celebration"))[0].slug == "brut"


def test_filters_and_exclusions():
    suggestions = Sommelier(CARDS).suggest(SommelierRequest(category="Белое", sweetness="dry", exclude_slugs=("riesling",)))

    assert [s.slug for s in suggestions] == ["riesling-2"]


def test_unknown_dish_and_questions_schema():
    with pytest.raises(ValueError):
        Sommelier(CARDS).suggest(SommelierRequest(dish="pizza-with-pineapple"))
    assert [q["id"] for q in questions()] == ["dish", "category", "sweetness", "body"]
