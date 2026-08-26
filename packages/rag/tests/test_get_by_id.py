"""get_by_id(id) -> Candidate|None — карточка по wine-slug / article:<slug>#<n>
/ winery:<slug>, для /wines/{id} (v0.2.1, предложение агента B)."""
from __future__ import annotations


def test_get_by_id_wine(tiny_index):
    c = tiny_index.get_by_id("red-dry-kuban-1")
    assert c is not None
    assert c.id == "red-dry-kuban-1"
    assert c.kind == "wine"
    assert c.meta["filters"]["color"] == "красное"


def test_get_by_id_article_chunk(tiny_index):
    c = tiny_index.get_by_id("article:test-article-fact#0")
    assert c is not None
    assert c.kind == "chunk"
    assert "танин" in c.text.lower()


def test_get_by_id_winery(tiny_index):
    c = tiny_index.get_by_id("winery:test-winery-a")
    assert c is not None
    assert c.kind == "winery"


def test_get_by_id_missing_returns_none(tiny_index):
    assert tiny_index.get_by_id("no-such-wine-slug") is None
    assert tiny_index.get_by_id("article:no-such-article#0") is None
    assert tiny_index.get_by_id("winery:no-such-winery") is None
