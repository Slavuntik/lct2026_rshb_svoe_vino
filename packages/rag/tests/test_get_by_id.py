"""get_by_id(id) -> Candidate|None — карточка по wine-slug / article:<slug>#<n>
/ winery:<slug>, для /wines/{id} (v0.2.1, предложение агента B).

Заодно проверяет форму Candidate.meta (контракт v0.3, ревью 02, блокер 1) —
get_by_id самый прямой путь до payload-кэша, без ranking/фильтров по пути.
"""
from __future__ import annotations


def test_get_by_id_wine(tiny_index):
    c = tiny_index.get_by_id("red-dry-kuban-1")
    assert c is not None
    assert c.id == "red-dry-kuban-1"
    assert c.kind == "wine"
    assert set(c.meta.keys()) == {"source", "derived"}
    assert c.meta["source"]["color"] == "красное"
    assert c.meta["derived"]["stillness"] == "тихое"


def test_get_by_id_article_chunk(tiny_index):
    c = tiny_index.get_by_id("article:test-article-fact#0")
    assert c is not None
    assert c.kind == "chunk"
    assert "танин" in c.text.lower()
    assert set(c.meta.keys()) == {"article_id", "title", "heading", "rubric"}
    assert c.meta["article_id"] == "test-article-fact"
    assert c.meta["rubric"] == "Гид"


def test_get_by_id_winery(tiny_index):
    c = tiny_index.get_by_id("winery:test-winery-a")
    assert c is not None
    assert c.kind == "winery"
    assert set(c.meta.keys()) == {"source"}
    assert c.meta["source"]["name"] == "Тестовая Винодельня А"


def test_get_by_id_missing_returns_none(tiny_index):
    assert tiny_index.get_by_id("no-such-wine-slug") is None
    assert tiny_index.get_by_id("article:no-such-article#0") is None
    assert tiny_index.get_by_id("winery:no-such-winery") is None
