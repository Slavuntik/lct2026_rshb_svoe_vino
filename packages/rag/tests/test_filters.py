"""Фильтры режут ДО векторов: «красное не приходит на запрос белое к рыбе».

Два уровня: чистая логика rag.filtering.passes_filters (без индекса) и
сквозной Retriever.search с реальным dense+BM25 на мини-фикстуре, где красные
и белые вина явно разведены по тексту и оба одинаково релевантны слову «вино».
"""
from __future__ import annotations

from rag.filtering import passes_filters
from rag.types import Filters


def test_passes_filters_color_pure_logic():
    red_payload = {"filters": {"color": "красное", "sugar": "сухое", "region": "kuban", "grapes": ["merlo"]}}
    white_payload = {"filters": {"color": "белое", "sugar": "сухое", "region": "krym", "grapes": ["shardone"]}}

    white_filter = Filters(color="белое")
    assert passes_filters(white_payload, white_filter) is True
    assert passes_filters(red_payload, white_filter) is False


def test_search_white_filter_excludes_red(tiny_index):
    results = tiny_index.search(
        "вино к рыбе", filters=Filters(color="белое"), collections=("wines",), top_k=8
    )
    assert results, "по мини-фикстуре должны найтись белые вина"
    for c in results:
        assert c.kind == "wine"
        assert c.meta["filters"]["color"] == "белое", f"красное просочилось: {c.id}"


def test_search_red_filter_excludes_white(tiny_index):
    results = tiny_index.search(
        "вино к рыбе", filters=Filters(color="красное"), collections=("wines",), top_k=8
    )
    assert results, "по мини-фикстуре должны найтись красные вина (фильтр не должен всё обнулять)"
    for c in results:
        assert c.meta["filters"]["color"] == "красное"


def test_search_without_filter_can_mix_colors(tiny_index):
    # Без фильтра оба цвета — валидные кандидаты (проверяем, что фильтр
    # реально что-то ОТСЕКАЕТ, а не просто всегда совпадает с одним цветом).
    results = tiny_index.search("вино", collections=("wines",), top_k=8)
    colors = {c.meta["filters"]["color"] for c in results}
    assert len(colors) > 1, "без фильтра ожидаем смесь цветов на мини-фикстуре"


def test_stillness_filter_on_analog_style_field():
    still_payload = {"filters": {"stillness": "тихое"}}
    sparkling_payload = {"filters": {"stillness": "игристое"}}
    f = Filters(stillness="игристое")
    assert passes_filters(sparkling_payload, f) is True
    assert passes_filters(still_payload, f) is False


def test_grapes_filter_requires_intersection():
    payload = {"filters": {"grapes": ["merlo", "kaberne-fran"]}}
    assert passes_filters(payload, Filters(grapes=["merlo"])) is True
    assert passes_filters(payload, Filters(grapes=["shardone"])) is False
