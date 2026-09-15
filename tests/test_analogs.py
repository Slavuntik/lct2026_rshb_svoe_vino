from winescan.product.analogs import AnalogFinder


def _card(slug, winery, category="Белое", grapes=("Рислинг",), sweetness="dry", sparkling=False, region="Кубань",
          description="свежий аромат цитрусов и яблока"):  # fmt: skip
    return {"slug": slug, "name": slug, "winery": winery, "category": category, "region": region,
            "grapes": list(grapes), "description": description,
            "attributes": {"sweetness": sweetness, "sparkling": sparkling}}  # fmt: skip


CARDS = {c["slug"]: c for c in [
    _card("base", "A"),
    _card("same-winery", "A"),
    _card("close", "B", description="свежий аромат цитрусов, яблока и белых цветов"),
    _card("other-grape", "C", grapes=("Шардоне",), region="Крым", description="сливочные ноты и дуб"),
    _card("red", "D", category="Красное"),
    _card("sparkling", "E", sparkling=True, sweetness="brut"),
]}  # fmt: skip


def test_analogs_exclude_same_winery_other_color_and_other_sparkling():
    slugs = [a.slug for a in AnalogFinder(CARDS).find("base")]

    assert slugs == ["close", "other-grape"]


def test_closest_analog_first_with_neutral_reasons():
    analogs = AnalogFinder(CARDS).find("base")

    assert analogs[0].slug == "close"
    assert "сорт: Рислинг" in analogs[0].reasons and "сладость: сухое" in analogs[0].reasons
    assert all("купи" not in reason.lower() for analog in analogs for reason in analog.reasons)
