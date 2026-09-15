import pytest

from winescan.catalog.attributes import parse_attributes


@pytest.mark.parametrize(
    ("name", "slug", "year", "sweetness", "sparkling"),
    [
        ("Алиготе Баррель, 2024", "aligote-barrel-2024", 2024, None, False),
        ("Кокур Сухое, 2025", "kokur-suhoe-2025", 2025, "dry", False),
        ("Конфесса Санджовезе Полусладкое", "soyuz-vino-konfessa-sandzhoveze-polusladkoe-krasnoe-11", None, "semi_sweet", False),
        ("Мускатель белый", "massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16", None, "sweet", False),
        ("Новый Свет выдержанное брют", "novyj-svet-vyderzhannoe-bryut", None, "brut", True),
        ("Спуманте Экстра Брют", "spumante-ekstra-bryut", None, "extra_brut", True),
        ("Новый Свет. Блан Де Нуар", "novyj-svet-blan-de-nuar", None, None, True),
        ("Совиньон Блан", "sovinon-blan", None, None, False),
        ("Пино Нуар полусухое 2025", "pino-nuar-polusuhoe-2025", 2025, "semi_dry", False),
    ],
)
def test_parse_attributes(name, slug, year, sweetness, sparkling):
    attrs = parse_attributes(name, slug)
    assert (attrs.year, attrs.sweetness, attrs.sparkling) == (year, sweetness, sparkling)


def test_name_has_priority_over_slug():
    # в slug «сухое», в названии «полусухое»: доверяем названию
    assert parse_attributes("Рислинг полусухое", "risling-suhoe").sweetness == "semi_dry"


def test_volume_from_name():
    assert parse_attributes("Аристов 8 Бьянко 0.7", "kuban-vino-aristov-8-byanko-07").volume_l == 0.7
    assert parse_attributes("Аристов 8 Бьянко", "kuban-vino-aristov-8-byanko").volume_l is None


def test_slug_abv_suffix_is_not_a_year():
    assert parse_attributes("Organic Syrah", "andryus-yutsis-organic-syrah-sira-krasnoe-suhoe-135").year is None
