import pytest

from winescan.search.text_match import LabelText, skeleton, text_score, token_similarity, tokens


def _card(name, winery, grapes=(), year=None, sweetness=None):
    return {"name": name, "winery": winery, "grapes": list(grapes),
            "attributes": {"year": year, "sweetness": sweetness}}  # fmt: skip


@pytest.mark.parametrize(("latin", "cyrillic_translit"), [("chardonnay", "shardone"), ("pinot", "pino"), ("noir", "nuar")])
def test_latin_and_cyrillic_spellings_are_similar(latin, cyrillic_translit):
    assert token_similarity(latin, cyrillic_translit) >= 0.8


def test_different_grapes_are_not_similar():
    assert token_similarity("riesling", "merlo") < 0.8
    assert skeleton("chardonnay") == skeleton("shardone") == "shrdn"


def test_tokens_transliterate_cyrillic():
    assert tokens("Мускатель Массандра белый, 2023") == ["muskatel", "massandra", "belyj", "2023"]


def test_label_text_extracts_year_and_sweetness():
    label = LabelText.from_ocr("ТАБИЯ винодельня Пино Нуар полусухое 2025")

    assert label.year == 2025 and label.sweetness == "semi_dry"


def test_series_members_are_separated_by_grape_on_label():
    label = LabelText.from_ocr("FANAGORIA 100 оттенков красного PINOT NOIR сухое 2021")
    pinot = _card("100 оттенков красного Пино Нуар", "Фанагория", ["Пино Нуар"], sweetness="dry")
    merlot = _card("100 оттенков красного Мерло", "Фанагория", ["Мерло"], sweetness="dry")

    assert text_score(pinot, label) > text_score(merlot, label)


def test_year_conflict_lowers_score():
    label = LabelText.from_ocr("Коммуналка Алиготе Баррель 2025")
    card_2024 = _card("Алиготе Баррель, 2024", "Коммуналка", ["Алиготе"], year=2024)
    card_2025 = _card("Алиготе Баррель, 2025", "Коммуналка", ["Алиготе"], year=2025)

    assert text_score(card_2025, label) - text_score(card_2024, label) == pytest.approx(0.6)


def test_empty_label_gives_zero():
    assert text_score(_card("Кокур", "Winepark"), LabelText.from_ocr("")) == 0.0
