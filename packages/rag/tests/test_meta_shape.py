"""Форма Candidate.meta зафиксирована контрактом v0.3 (ревью 02, блокер 1):
API падал 500 на реальном RAG, читая meta["source"], которого не было.

  kind=wine:   {"source": {...}, "derived": {...}}   — блоки карточки целиком
  kind=chunk:  {"article_id", "title", "heading", "rubric"}
  kind=winery: {"source": {...}}

Проверяем по ВСЕМ методам, отдающим Candidate — форма не должна зависеть от
того, каким путём получен результат (search/similar/analog_for_style/
get_by_id/resolve_label/candidates_for_taste).
"""
from __future__ import annotations

from rag.meta import public_meta


def _assert_wine_meta(meta: dict):
    assert "source" in meta and "derived" in meta
    assert isinstance(meta["source"], dict) and meta["source"]
    assert isinstance(meta["derived"], dict)


def _assert_chunk_meta(meta: dict):
    assert set(meta.keys()) == {"article_id", "title", "heading", "rubric"}


def _assert_winery_meta(meta: dict):
    assert set(meta.keys()) == {"source"}
    assert isinstance(meta["source"], dict) and meta["source"]


def test_public_meta_unit_wine():
    payload = {"kind": "wine", "source": {"name": "X"}, "derived": {"stillness": "тихое"}, "filters": {"junk": 1}}
    meta = public_meta(payload)
    assert meta == {"source": {"name": "X"}, "derived": {"stillness": "тихое"}}


def test_public_meta_unit_chunk():
    payload = {
        "kind": "chunk",
        "article_id": "a1",
        "title": "T",
        "heading": "H",
        "filters": {"rubric": "Гид", "author": "should not leak"},
    }
    meta = public_meta(payload)
    assert meta == {"article_id": "a1", "title": "T", "heading": "H", "rubric": "Гид"}


def test_public_meta_unit_winery():
    payload = {"kind": "winery", "source": {"name": "W"}, "filters": {"region": "kuban"}}
    assert public_meta(payload) == {"source": {"name": "W"}}


def test_public_meta_missing_blocks_defaults_empty_dicts():
    assert public_meta({"kind": "wine"}) == {"source": {}, "derived": {}}
    assert public_meta({"kind": "winery"}) == {"source": {}}


def test_search_meta_shape_by_kind(tiny_index):
    seen_kinds = set()
    for c in tiny_index.search("вино к рыбе", collections=("wines", "knowledge", "wineries"), top_k=20):
        seen_kinds.add(c.kind)
        if c.kind == "wine":
            _assert_wine_meta(c.meta)
        elif c.kind == "chunk":
            _assert_chunk_meta(c.meta)
        elif c.kind == "winery":
            _assert_winery_meta(c.meta)
        else:
            raise AssertionError(f"неизвестный kind: {c.kind}")
    assert seen_kinds, "ожидали хоть какие-то результаты на мини-фикстуре"


def test_similar_meta_shape(tiny_index):
    results = tiny_index.similar("red-dry-kuban-1", top_k=5)
    assert results
    for c in results:
        assert c.kind == "wine"
        _assert_wine_meta(c.meta)


def test_analog_for_style_meta_shape(tiny_index):
    results = tiny_index.analog_for_style("prosecco", top_k=8)
    assert results
    for c in results:
        assert c.kind == "wine"
        _assert_wine_meta(c.meta)


def test_get_by_id_meta_shape_all_kinds(tiny_index):
    _assert_wine_meta(tiny_index.get_by_id("red-dry-kuban-1").meta)
    _assert_chunk_meta(tiny_index.get_by_id("article:test-article-fact#0").meta)
    _assert_winery_meta(tiny_index.get_by_id("winery:test-winery-a").meta)


def test_resolve_label_meta_has_wine_shape_plus_match_info(tiny_index):
    results = tiny_index.resolve_label("Тестовый Совиньон Блан Тестовая Винодельня Б")
    assert results
    meta = results[0].meta
    _assert_wine_meta(meta)  # source/derived должны быть, несмотря на доп. поля
    assert "match_score" in meta and "low_confidence" in meta


def test_candidates_for_taste_meta_shape(tiny_index):
    deck = tiny_index.candidates_for_taste(exclude_ids=[], limit=20)
    assert deck
    for c in deck:
        assert c.kind == "wine"
        _assert_wine_meta(c.meta)
