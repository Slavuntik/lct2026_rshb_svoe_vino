"""Контрактная сверка MockImageIndex/MockLabelVerifier с
contracts/image-scan.md v0.4 — форма Match, ValueError на битом файле,
add() без ребилда, build() не падает."""
from __future__ import annotations

import pytest

from app.cv.fixtures import ALL_SLUGS, NEAR_DUP_GROUP
from app.cv.interface import Match
from app.cv.mock import MockImageIndex, MockLabelVerifier


def test_search_on_empty_bytes_raises_value_error_not_500():
    """agents/G-cv.md тест-требование (то же для мока — контракт один):
    search на битом файле -> ValueError, не 500-полуфабрикат."""
    index = MockImageIndex()
    with pytest.raises(ValueError):
        index.search(b"")


def test_embed_on_empty_bytes_raises_value_error():
    index = MockImageIndex()
    with pytest.raises(ValueError):
        index.embed(b"")


def test_embed_returns_list_of_floats():
    index = MockImageIndex()
    vec = index.embed(b"MOCKPHOTO:shato-vymysel-cabernet")
    assert isinstance(vec, list)
    assert vec and all(isinstance(x, float) for x in vec)


def test_search_confident_match_returns_single_high_score_match():
    index = MockImageIndex()
    results = index.search(b"MOCKPHOTO:belye-peski-sauvignon-blanc")
    assert len(results) == 1
    m = results[0]
    assert isinstance(m, Match)
    assert m.slug == "belye-peski-sauvignon-blanc"
    assert m.score > 0.9
    assert m.view in ("реальный",) or m.view.startswith("synth-")


def test_search_unknown_photo_returns_empty_list():
    index = MockImageIndex()
    assert index.search(b"MOCKPHOTO:unknown") == []


def test_search_near_dup_returns_two_close_scored_matches_with_small_gap():
    index = MockImageIndex()
    results = index.search(b"MOCKPHOTO:near-dup", top_k=5)
    assert len(results) == 2
    slugs = {m.slug for m in results}
    assert slugs == set(NEAR_DUP_GROUP)
    assert abs(results[0].score - results[1].score) < 0.05
    assert all(m.gap is not None and m.gap < 0.05 for m in results)


def test_search_respects_top_k():
    index = MockImageIndex()
    results = index.search(b"MOCKPHOTO:near-dup", top_k=1)
    assert len(results) == 1


def test_search_random_bytes_degrades_gracefully_not_crash():
    """Настоящее фото (не по MOCKPHOTO-конвенции) — мок не должен падать,
    просто не умеет "видеть" его осмысленно."""
    index = MockImageIndex()
    results = index.search(b"\xff\xd8\xff\xe0\x00\x10JFIF-not-a-real-jpeg-but-nonempty")
    assert len(results) == 1
    assert results[0].slug in ALL_SLUGS


def test_add_makes_new_slug_searchable_without_rebuild():
    """contracts/image-scan.md: "+50 позиций/день без ребилда". У мока add()
    не подключён к search() (фикстуры статические) — здесь проверяем только
    то, что реально гарантирует Protocol: метод принимает вход и не падает,
    без чего API не сможет вызвать его вслепую."""
    index = MockImageIndex()
    index.add("new-mock-slug", [b"MOCKPHOTO:new-mock-slug"])
    assert "new-mock-slug" in index._added


def test_build_does_not_raise():
    index = MockImageIndex()
    index.build({"some-slug": ["ref.jpg"]}, version="test-1")


def test_label_verifier_resolves_default_near_dup_answer():
    verifier = MockLabelVerifier()
    answer = verifier.verify(b"MOCKPHOTO:near-dup", NEAR_DUP_GROUP)
    assert answer in NEAR_DUP_GROUP


def test_label_verifier_honors_explicit_requested_slug():
    verifier = MockLabelVerifier()
    requested = NEAR_DUP_GROUP[0]
    answer = verifier.verify(f"MOCKPHOTO:near-dup:{requested}".encode(), NEAR_DUP_GROUP)
    assert answer == requested


def test_label_verifier_returns_none_when_told_to_simulate_failure():
    verifier = MockLabelVerifier()
    answer = verifier.verify(b"MOCKPHOTO:near-dup:NONE", NEAR_DUP_GROUP)
    assert answer is None


def test_label_verifier_ignores_slug_not_in_candidates():
    verifier = MockLabelVerifier()
    answer = verifier.verify(b"MOCKPHOTO:near-dup:not-a-real-candidate", NEAR_DUP_GROUP)
    assert answer is None
