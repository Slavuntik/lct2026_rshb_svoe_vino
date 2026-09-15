from winescan.search.fields import LabelFields, field_score, parse_fields

BELYY = {"name": "Мускатель белый", "winery": "Массандра", "category": "Белое", "grapes": ["Белые сорта винограда"],
         "attributes": {"year": None, "sweetness": "sweet", "sparkling": False}}  # fmt: skip
ROZOVYY = {"name": "Мускатель розовый", "winery": "Массандра", "category": "Розовое", "grapes": ["Белые сорта винограда"],
           "attributes": {"year": None, "sweetness": "sweet", "sparkling": False}}  # fmt: skip


def test_parse_fields_from_fenced_json_with_nulls():
    raw = """```json
{"winery": "Массандра", "name": "Мускатель белый", "grapes": null, "year": "2023",
 "color": "белое", "sweetness": "сладкое", "sparkling": false, "text": "МАССАНДРА 1894 МУСКАТЕЛЬ"}
```"""
    fields = parse_fields(raw)

    assert fields.winery == "Массандра" and fields.year == 2023
    assert fields.color == "Белое" and fields.sweetness == "sweet" and fields.sparkling is False
    assert fields.grapes == ()


def test_parse_fields_garbage_keeps_text_only():
    fields = parse_fields("не могу прочитать")

    assert fields.winery == "" and fields.text == "не могу прочитать"


def test_field_score_prefers_matching_color_and_name():
    fields = LabelFields(winery="Массандра", name="Мускатель белый", color="Белое", year=2023)

    assert field_score(BELYY, fields) > field_score(ROZOVYY, fields) + 0.5


def test_empty_fields_score_zero():
    assert field_score(BELYY, LabelFields()) == 0.0
