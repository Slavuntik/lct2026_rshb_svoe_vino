"""list_reference_styles(top_n) — популярные стили по частоте в
filters.reference_style_matches каталога (подсказка для 404 /analogs, v0.2.1)."""
from __future__ import annotations

from rag.styles import StyleMatcher

_STYLES = [
    {"slug": "prosecco", "name": "Просекко", "country": "Италия", "color": "белое", "stillness": "игристое", "sugar": [], "sensory": {}},
    {"slug": "chablis", "name": "Шабли", "country": "Франция", "color": "белое", "stillness": "тихое", "sugar": [], "sensory": {}},
    {"slug": "rioja-crianza", "name": "Риоха Крианса", "country": "Испания", "color": "красное", "stillness": "тихое", "sugar": [], "sensory": {}},
]


def _wine(styles):
    return {"filters": {"reference_style_matches": styles}}


_WINES = (
    [_wine(["prosecco"])] * 5
    + [_wine(["chablis"])] * 3
    + [_wine(["rioja-crianza"])] * 1
    + [_wine([])] * 2  # вина без стиля не должны мешать подсчёту
)


def test_list_popular_orders_by_frequency():
    sm = StyleMatcher(styles=_STYLES)
    top = sm.list_popular(_WINES, top_n=5)
    assert [s["slug"] for s in top] == ["prosecco", "chablis", "rioja-crianza"]
    assert top[0] == {"slug": "prosecco", "name": "Просекко", "country": "Италия"}


def test_list_popular_respects_top_n():
    sm = StyleMatcher(styles=_STYLES)
    top = sm.list_popular(_WINES, top_n=2)
    assert len(top) == 2
    assert [s["slug"] for s in top] == ["prosecco", "chablis"]


def test_list_popular_empty_catalog_returns_empty():
    sm = StyleMatcher(styles=_STYLES)
    assert sm.list_popular([], top_n=5) == []


def test_list_reference_styles_end_to_end_on_tiny_index(tiny_index):
    # Мини-фикстура: ровно одно вино со style-тегом "prosecco" (см. conftest.py).
    top = tiny_index.list_reference_styles(top_n=5)
    slugs = [s["slug"] for s in top]
    assert "prosecco" in slugs
    for s in top:
        assert set(s.keys()) == {"slug", "name", "country"}
