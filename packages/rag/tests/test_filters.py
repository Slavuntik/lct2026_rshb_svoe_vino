"""Фильтры режут ДО векторов: «красное не приходит на запрос белое к рыбе».

Два уровня: чистая логика rag.filtering.passes_filters (без индекса) и
сквозной Retriever.search с реальным dense+BM25 на мини-фикстуре, где красные
и белые вина явно разведены по тексту и оба одинаково релевантны слову «вино».
"""
from __future__ import annotations

from dataclasses import dataclass

from rag.filtering import passes_filters
from rag.types import Filters, filters_active


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
        assert c.meta["source"]["color"] == "белое", f"красное просочилось: {c.id}"


def test_search_red_filter_excludes_white(tiny_index):
    results = tiny_index.search(
        "вино к рыбе", filters=Filters(color="красное"), collections=("wines",), top_k=8
    )
    assert results, "по мини-фикстуре должны найтись красные вина (фильтр не должен всё обнулять)"
    for c in results:
        assert c.meta["source"]["color"] == "красное"


def test_search_without_filter_can_mix_colors(tiny_index):
    # Без фильтра оба цвета — валидные кандидаты (проверяем, что фильтр
    # реально что-то ОТСЕКАЕТ, а не просто всегда совпадает с одним цветом).
    results = tiny_index.search("вино", collections=("wines",), top_k=8)
    colors = {c.meta["source"]["color"] for c in results}
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


# --------------------------------------------------------------------------
# 22.09 (reports/backend-chat-retrieval.md): filters_active() — сигнал
# rag.intent.classify() и rag.base.Retriever.search() (см. те модули).
# --------------------------------------------------------------------------

def test_filters_active_none_is_false():
    assert filters_active(None) is False


def test_filters_active_empty_object_is_false():
    assert filters_active(Filters()) is False


def test_filters_active_true_for_each_single_field():
    assert filters_active(Filters(color="красное")) is True
    assert filters_active(Filters(sugar="сухое")) is True
    assert filters_active(Filters(region="krym")) is True
    assert filters_active(Filters(grapes=["merlo"])) is True
    assert filters_active(Filters(stillness="игристое")) is True


def test_filters_active_empty_grapes_list_is_still_inactive():
    # grapes=[] — дефолт dataclass, не "признак распознан".
    assert filters_active(Filters(grapes=[])) is False


def test_filters_active_is_duck_typed_not_isinstance_bound():
    """Критично для apps/api/app/rag/interface.py::Filters — ОТДЕЛЬНЫЙ класс
    с теми же полями (структурный контракт, "продублирован дословно"), без
    общего базового класса и без гарантии тех же методов. filters_active()
    обязан работать через getattr на ЛЮБОМ объекте с этими атрибутами, не
    только на rag.types.Filters — иначе первый же боевой вызов из apps/api
    ловит AttributeError (см. отчёт: ровно так и было с методом до правки)."""

    @dataclass
    class _DuckFilters:
        color: str | None = None
        sugar: str | None = None
        region: str | None = None
        grapes: list[str] | None = None
        stillness: str | None = None

    assert filters_active(_DuckFilters()) is False
    assert filters_active(_DuckFilters(color="белое")) is True

    class _NotAFilterAtAll:
        pass

    # Отсутствующие атрибуты — getattr(..., default) тихо считает их "не задано",
    # не падает AttributeError.
    assert filters_active(_NotAFilterAtAll()) is False
