"""candidates_for_taste(exclude_ids, limit) — колода для свайп-дегустации:
разнообразие по цвету/региону/стилю + exclude_ids реально исключает (v0.2.3,
метод жил только в моке API — без контракта /taste/candidates бы сломался)."""
from __future__ import annotations

from rag.taste import build_taste_deck

_WINES = [
    {
        "id": f"w{i}",
        "kind": "wine",
        "text": f"вино {i}",
        "url": f"https://x/{i}",
        "filters": {"color": c, "region": r, "reference_style_matches": [s] if s else []},
        # meta наружу строится из source/derived (контракт v0.3, блокер 1) —
        # "filters" выше остаётся внутренним (используется diversity-логикой).
        "source": {"color": c, "region_name": r},
        "derived": {"reference_style_matches": [s] if s else []},
    }
    for i, (c, r, s) in enumerate(
        [
            ("красное", "kuban", "bordeaux-right-bank"),
            ("белое", "krym", "chablis"),
            ("розовое", "dagestan", "provence-rose"),
            ("красное", "krym", "brunello"),
            ("белое", "kuban", None),
            ("оранжевое", "krym", "georgian-qvevri-amber"),
            ("красное", "dagestan", None),
            ("белое", "dolina-dona", "sancerre"),
        ]
    )
]


def test_deck_respects_limit_and_excludes_ids():
    deck = build_taste_deck(_WINES, exclude_ids=["w0", "w1"], limit=5, seed=42)
    ids = [c.id for c in deck]
    assert "w0" not in ids
    assert "w1" not in ids
    assert len(ids) == 5
    assert len(set(ids)) == 5  # без повторов


def test_deck_is_diverse_in_color():
    deck = build_taste_deck(_WINES, exclude_ids=[], limit=6, seed=1)
    colors = {c.meta["source"]["color"] for c in deck}
    # в пуле 4 разных цвета — колода из 6 карт должна показать разнообразие,
    # а не 6 одинаковых цветов
    assert len(colors) >= 3


def test_deck_deterministic_for_same_seed():
    deck1 = build_taste_deck(_WINES, exclude_ids=[], limit=8, seed=123)
    deck2 = build_taste_deck(_WINES, exclude_ids=[], limit=8, seed=123)
    assert [c.id for c in deck1] == [c.id for c in deck2]


def test_deck_differs_for_different_seed():
    deck1 = build_taste_deck(_WINES, exclude_ids=[], limit=8, seed=1)
    deck2 = build_taste_deck(_WINES, exclude_ids=[], limit=8, seed=2)
    # хотя бы порядок должен отличаться (разные дни -> разная колода)
    assert [c.id for c in deck1] != [c.id for c in deck2]


def test_deck_handles_exclude_all_returns_empty():
    all_ids = [w["id"] for w in _WINES]
    assert build_taste_deck(_WINES, exclude_ids=all_ids, limit=5) == []


def test_candidates_for_taste_end_to_end_on_tiny_index(tiny_index):
    deck = tiny_index.candidates_for_taste(exclude_ids=["red-dry-kuban-1"], limit=20)
    ids = [c.id for c in deck]
    assert "red-dry-kuban-1" not in ids
    assert len(ids) <= 20
    for c in deck:
        assert c.kind == "wine"
