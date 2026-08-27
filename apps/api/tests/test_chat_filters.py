"""app/chat/filters.py::extract_filters — детерминированное извлечение
Filters из реплики /chat, без LLM (contracts/rag-interface.md v0.2.4)."""
from __future__ import annotations

from app.chat.filters import extract_filters


def test_canonical_example_red_steak_from_kuban():
    f = extract_filters("Хочу красное вино к стейку из Кубани")
    assert f.color == "красное"
    assert f.region == "kuban"
    assert f.sugar is None


def test_white_and_dry():
    f = extract_filters("Посоветуй что-то белое и сухое к рыбе")
    assert f.color == "белое"
    assert f.sugar == "сухое"


def test_semi_sweet_is_not_confused_with_sweet():
    f = extract_filters("Ищу полусладкое розовое вино на вечер")
    assert f.color == "розовое"
    assert f.sugar == "полусладкое"


def test_semi_dry_is_not_confused_with_dry():
    f = extract_filters("Хочу полусухое белое")
    assert f.color == "белое"
    assert f.sugar == "полусухое"


def test_brut_and_region_crimea():
    f = extract_filters("Что-нибудь брют, желательно из Крыма")
    assert f.sugar == "брют"
    assert f.region == "krym"


def test_region_dagestan():
    f = extract_filters("Порекомендуй вино из Дагестана к сыру")
    assert f.region == "dagestan"


def test_region_don_via_adjective_form():
    f = extract_filters("Хочу попробовать донское красное")
    assert f.region == "dolina-dona"
    assert f.color == "красное"


def test_message_with_no_recognizable_filters_returns_all_none():
    f = extract_filters("Просто хочу вкусного вина, удиви меня")
    assert f.color is None
    assert f.sugar is None
    assert f.region is None


def test_extra_brut_takes_priority_over_plain_brut():
    f = extract_filters("Хочу экстра брют на праздник")
    assert f.sugar == "экстра брют"
