"""Контрактная сверка MockImageIndex/MockLabelVerifier с
contracts/image-scan.md v0.4 — форма Match, ValueError на битом файле,
add() без ребилда, build() не падает."""
from __future__ import annotations

import pytest

from app.cv.fixtures import ALL_SLUGS, NEAR_DUP_GROUP
from app.cv.interface import Match, VerifyCandidate
from app.cv.mock import MockImageIndex, MockLabelVerifier


def _candidates(slugs: list[str]) -> list[VerifyCandidate]:
    """v0.4.4: verify() принимает VerifyCandidate ({slug, name, vintage}), не
    голые slug'и — мок в этих тестах не смотрит на name/vintage вообще (см.
    докстринг MockLabelVerifier), значения тут произвольные."""
    return [VerifyCandidate(slug=s, name=s, vintage=None) for s in slugs]


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
    assert m.view in ("real",) or m.view.startswith("synth-")


def test_search_unknown_photo_returns_empty_list():
    index = MockImageIndex()
    assert index.search(b"MOCKPHOTO:unknown") == []


def test_search_near_dup_returns_group_plus_boundary_match_with_small_gap():
    """v0.4.2: contracts/image-scan.md определяет gap как отрыв ИМЕННО до
    следующего НЕ-той-же-группы кандидата (не до соседа по рангу) —
    run_photo_scan() отфильтровывает кандидатов на OCR по score > (top.score
    - top.gap) (app/cv/service.py). Мок обязан отдавать не только 2 члена
    группы, но и границу — иначе фильтр нечего проверять на реалистичных
    данных (найдено интеграционным тестом на реальном ImageIndex, см.
    reports/b-report.md)."""
    index = MockImageIndex()
    results = index.search(b"MOCKPHOTO:near-dup", top_k=5)
    assert len(results) == 3
    group_slugs = {m.slug for m in results[:2]}
    assert group_slugs == set(NEAR_DUP_GROUP)
    top = results[0]
    assert abs(top.score - results[1].score) < 0.05  # два члена группы — близкий score
    assert top.gap is not None and top.gap < 0.05
    # Третья позиция — граница группы: другой слаг, score ровно top.score - gap.
    assert results[2].slug not in NEAR_DUP_GROUP
    assert round(top.score - top.gap, 4) == results[2].score


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
    answer = verifier.verify(b"MOCKPHOTO:near-dup", _candidates(NEAR_DUP_GROUP))
    assert answer in NEAR_DUP_GROUP


def test_label_verifier_honors_explicit_requested_slug():
    verifier = MockLabelVerifier()
    requested = NEAR_DUP_GROUP[0]
    answer = verifier.verify(f"MOCKPHOTO:near-dup:{requested}".encode(), _candidates(NEAR_DUP_GROUP))
    assert answer == requested


def test_label_verifier_returns_none_when_told_to_simulate_failure():
    verifier = MockLabelVerifier()
    answer = verifier.verify(b"MOCKPHOTO:near-dup:NONE", _candidates(NEAR_DUP_GROUP))
    assert answer is None


def test_label_verifier_ignores_slug_not_in_candidates():
    verifier = MockLabelVerifier()
    answer = verifier.verify(b"MOCKPHOTO:near-dup:not-a-real-candidate", _candidates(NEAR_DUP_GROUP))
    assert answer is None


def test_label_verifier_receives_catalog_metadata_shape():
    """v0.4.4: сам Protocol теперь про VerifyCandidate — минимальная проверка,
    что мок принимает форму {slug, name, vintage} и достаёт из неё slug (а не
    падает, приняв дикты за строки)."""
    verifier = MockLabelVerifier()
    candidates: list[VerifyCandidate] = [
        VerifyCandidate(slug=NEAR_DUP_GROUP[0], name="Тайное вино 2022", vintage=2022),
        VerifyCandidate(slug=NEAR_DUP_GROUP[1], name="Тайное вино 2023", vintage=2023),
    ]
    answer = verifier.verify(f"MOCKPHOTO:near-dup:{NEAR_DUP_GROUP[1]}".encode(), candidates)
    assert answer == NEAR_DUP_GROUP[1]
