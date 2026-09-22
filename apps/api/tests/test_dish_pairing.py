"""app/dish_pairing.py — подбор вин к распознанному/выбранному блюду
(contracts/post-scan.md v1.1 по брифу тимлида 22.09). Два яруса: каталог
(тег буквально в source.food_pairings) -> движок правил
(pipeline/ref/food_pairing_rules.yaml, блюдо -> вино). Диверсификация: не
больше 2 вин одной винодельни, всего 6. Пул кандидатов —
`candidates_for_taste()`, не `search()` (см. докстринг app/dish_pairing.py —
`search("BBQ", ...)` эмпирически отдавал ПУСТОЙ список на MockRetriever).

Часть тестов идёт через `app`-фикстуру (MockRetriever, реальные 6 фикстурных
вин app/rag/fixtures.py — `tihaya-gavan-pinot-noir` несёт тег "Сыры",
`rozovyy-mirazh` — "Азиатская кухня", ровно как в каталоге портала). Тест
диверсификации по винодельне использует свой стаб-Retriever (>2 вина одной
винодельни, каких нет во влияющих на другие роли фикстурах)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.dish_pairing import select_wines_for_dish
from app.rag.interface import Candidate


def _wine_candidate(
    wine_id: str, *, winery: str, food_pairings: list[str] | None = None,
    sensory: dict | None = None, color: str = "красное", score: float = 1.0,
) -> Candidate:
    source = {
        "name": f"Тестовое вино {wine_id}", "winery": winery, "winery_name": winery.title(),
        "color": color, "sugar_category": "сухое", "image_url": f"https://example.com/{wine_id}.webp",
        "food_pairings": food_pairings or [],
    }
    derived = {"sensory": sensory} if sensory else {}
    return Candidate(id=wine_id, kind="wine", score=score, text="", url=f"https://example.com/{wine_id}", meta={
        "source": source, "derived": derived,
    })


@dataclass
class _StubRetriever:
    """Реализует только `candidates_for_taste()` — единственный метод
    Retriever, который трогает `select_wines_for_dish`."""
    candidates: list[Candidate] = field(default_factory=list)
    taste_calls: list[tuple[list[str], int]] = field(default_factory=list)

    def candidates_for_taste(self, exclude_ids, limit=20):
        self.taste_calls.append((list(exclude_ids), limit))
        return self.candidates[:limit]

    def search(self, query, *, filters=None, collections=None, top_k=8): raise NotImplementedError
    def resolve_label(self, text, hints=None): raise NotImplementedError
    def similar(self, wine_id, top_k=6): raise NotImplementedError
    def analog_for_style(self, style_slug, *, filters=None, top_k=12): raise NotImplementedError
    def resolve_style(self, query): raise NotImplementedError
    def get_by_id(self, id): raise NotImplementedError
    def list_reference_styles(self, top_n=5): raise NotImplementedError


def _settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------
# Через фикстуры MockRetriever (реальные 6 вин app/rag/fixtures.py) — `app`
# фикстура (tests/conftest.py) гарантирует чистый env (RAG_PROVIDER=mock).
# --------------------------------------------------------------------------

def test_catalog_tier_finds_exact_tag_match(app):
    """tihaya-gavan-pinot-noir несёт "Сыры" буквально в food_pairings —
    обязан прийти basis=catalog первым, с детерминированным reason."""
    retriever, settings = app.state.retriever, app.state.settings
    wines = select_wines_for_dish(retriever, "Сыры", settings)
    assert wines, "хотя бы одно вино обязано найтись"
    catalog_items = [w for w in wines if w["basis"] == "catalog"]
    assert any(w["wine_id"] == "tihaya-gavan-pinot-noir" for w in catalog_items)
    top = next(w for w in catalog_items if w["wine_id"] == "tihaya-gavan-pinot-noir")
    assert top["reason"] == "Портал рекомендует это вино к категории «Сыры»."
    assert top["name"] and top["winery"]


def test_rules_tier_fills_remaining_slots_with_positive_score_reason(app):
    """BBQ ни у одного из 6 фикстурных вин нет в food_pairings буквально —
    чистый rules-ярус; reason обязан быть дословным explain из
    food_pairing_rules.yaml, не выдумкой."""
    retriever, settings = app.state.retriever, app.state.settings
    wines = select_wines_for_dish(retriever, "BBQ", settings)
    assert wines
    assert all(w["basis"] == "rules" for w in wines)
    assert all(w["reason"] for w in wines)  # непустой человекочитаемый текст


def test_at_most_six_wines_and_max_two_per_winery(app):
    retriever, settings = app.state.retriever, app.state.settings
    for category in ("Сыры", "Азиатская кухня", "BBQ", "Салаты", "Блюда из птицы"):
        wines = select_wines_for_dish(retriever, category, settings)
        assert len(wines) <= 6
        wineries = [w["winery"] for w in wines]
        assert all(wineries.count(w) <= 2 for w in wineries)


def test_deterministic_across_repeated_calls(app):
    retriever, settings = app.state.retriever, app.state.settings
    r1 = select_wines_for_dish(retriever, "Сыры", settings)
    r2 = select_wines_for_dish(retriever, "Сыры", settings)
    assert r1 == r2


def test_pool_requested_without_exclusions():
    retriever = _StubRetriever(candidates=[])
    select_wines_for_dish(retriever, "Сыры", _settings())
    assert retriever.taste_calls == [([], 30)]


def test_empty_pool_returns_empty_list_not_exception():
    retriever = _StubRetriever(candidates=[])
    assert select_wines_for_dish(retriever, "Сыры", _settings()) == []


# --------------------------------------------------------------------------
# Диверсификация по винодельне — стаб-Retriever с намеренно "плотным" пулом
# --------------------------------------------------------------------------

def test_winery_diversity_cap_applies_across_both_tiers():
    """4 вина одной винодельни ("dense-winery"): 2 с тегом-каталогом (должны
    выиграть первыми — basis=catalog), ещё 2 с тем же профилем через rules —
    диверсификация обязана оставить РОВНО 2 вина этой винодельни всего,
    несмотря на то, что оба яруса готовы отдать больше."""
    sensory_bbq_friendly = {
        "sweetness": 0.1, "acidity": 0.5, "tannin": 0.7, "body": 0.8,
        "oak": 0.5, "aromatic_intensity": 0.8, "bubbles": 0.0,
    }
    candidates = [
        _wine_candidate("dense-1", winery="dense-winery", food_pairings=["BBQ"]),
        _wine_candidate("dense-2", winery="dense-winery", food_pairings=["BBQ"]),
        _wine_candidate("dense-3", winery="dense-winery", sensory=sensory_bbq_friendly),
        _wine_candidate("dense-4", winery="dense-winery", sensory=sensory_bbq_friendly),
        _wine_candidate("other-1", winery="other-winery", sensory=sensory_bbq_friendly),
    ]
    retriever = _StubRetriever(candidates=candidates)
    wines = select_wines_for_dish(retriever, "BBQ", _settings())

    dense_count = sum(1 for w in wines if w["winery"] == "Dense-Winery")
    assert dense_count == 2, f"диверсификация обязана ограничить винодельню 2 вина, получили {dense_count}"
    # каталожный ярус выигрывает у rules при равной винодельне — dense-1/dense-2
    dense_ids = {w["wine_id"] for w in wines if w["winery"] == "Dense-Winery"}
    assert dense_ids == {"dense-1", "dense-2"}
    assert any(w["wine_id"] == "other-1" for w in wines)


def test_catalog_tier_beats_rules_tier_when_both_available_for_same_winery():
    candidates = [
        _wine_candidate("rules-only", winery="w1", sensory={
            "sweetness": 0.1, "acidity": 0.5, "tannin": 0.7, "body": 0.8,
            "oak": 0.5, "aromatic_intensity": 0.8, "bubbles": 0.0,
        }),
        _wine_candidate("catalog-tag", winery="w2", food_pairings=["BBQ"]),
    ]
    retriever = _StubRetriever(candidates=candidates)
    wines = select_wines_for_dish(retriever, "BBQ", _settings())
    assert wines[0]["wine_id"] == "catalog-tag"
    assert wines[0]["basis"] == "catalog"
    assert wines[1]["wine_id"] == "rules-only"
    assert wines[1]["basis"] == "rules"


def test_wine_without_usable_vector_is_skipped_not_crashed():
    """Ни food_pairings-тега, ни надёжной sensory (< 7 осей), ни пригодного
    color — heuristic-вектор тоже недоступен (build_heuristic_wine_vector
    возвращает None) — вино просто не попадает в подбор, не 500."""
    candidates = [
        _wine_candidate("no-signal", winery="w1", color="", sensory=None),
    ]
    retriever = _StubRetriever(candidates=candidates)
    assert select_wines_for_dish(retriever, "BBQ", _settings()) == []


def test_hard_block_excludes_wine_even_if_it_would_otherwise_score():
    """dry_wine_with_dessert (food_pairing_rules.yaml hard_blocks): sweetness
    блюда >=0.6 И sweetness вина <=0.15 -> вино исключено целиком, даже если
    остальные оси дали бы положительный score. "Выпечка и десерты" — единственный
    тег с dish.sweetness=0.8 среди 9 канонических (portal_tag_defaults)."""
    dry_sensory = {
        "sweetness": 0.05, "acidity": 0.6, "tannin": 0.1, "body": 0.6,
        "oak": 0.3, "aromatic_intensity": 0.7, "bubbles": 0.0,
    }
    candidates = [_wine_candidate("bone-dry", winery="w1", sensory=dry_sensory)]
    retriever = _StubRetriever(candidates=candidates)
    assert select_wines_for_dish(retriever, "Выпечка и десерты", _settings()) == []
