"""app/dish_pairing.py — подбор вин к распознанному/выбранному блюду
(contracts/post-scan.md v1.1 §4.3). Пул кандидатов — ВЕСЬ каталог, не
выборка (тимлид 22.09 отклонил первую версию на `candidates_for_taste()`/
`search()`, 30 из 2103 — "колода свайп-дегустации... к стейку предложат
лучшее из колоды, а не из каталога"). Все тесты подменяют
`_iter_catalog_cards()` целиком (бриф тимлида, п.4: "источник каталога
подменяемым, а не обход через search") — ни `case_catalog.json`, ни
`build_wine_card`, ни сеть не трогаются."""
from __future__ import annotations

from app import dish_pairing
from app.config import Settings
from app.dish_pairing import _wine_vector, select_wines_for_dish

_BBQ_SENSORY = {
    "sweetness": 0.1, "acidity": 0.5, "tannin": 0.7, "body": 0.8,
    "oak": 0.5, "aromatic_intensity": 0.8, "bubbles": 0.0,
}


def _card(
    wine_id: str, *, winery: str, food_pairings: list[str] | None = None,
    sensory: dict | None = None, color: str = "красное", sugar: str = "сухое",
) -> tuple[str, dict, dict | None]:
    """`(wine_id, source, wine_vector)` — та же форма, что `_build_catalog_cards()`
    отдаёт в проде, посчитанная через настоящий `_wine_vector()` (не
    руками) — фикстуры остаются честными относительно реальной sensory→
    heuristic логики."""
    source = {
        "name": f"Тестовое вино {wine_id}", "winery": winery, "winery_name": winery.title(),
        "color": color, "sugar_category": sugar, "image_url": f"https://example.com/{wine_id}.webp",
        "food_pairings": food_pairings or [],
    }
    derived = {"sensory": sensory} if sensory else {}
    return (wine_id, source, _wine_vector(source, derived))


def _patch_catalog(monkeypatch, cards: list[tuple[str, dict, dict | None]]) -> None:
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda retriever, settings: tuple(cards))


def _settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------
# Регресс-тест на прежний дефект (тимлид 22.09): лучшее по тегу вино лежит
# вне ЛЮБОЙ выборки/колоды фиксированного размера (30) — с полным каталогом
# оно обязано попасть в выдачу всё равно.
# --------------------------------------------------------------------------

def test_best_tagged_wine_beyond_any_fixed_size_sample_still_appears(monkeypatch):
    """40 вин, ТОЛЬКО последнее (по любому порядку добавления/выборки —
    имя нарочно НЕ начинается с ранних букв алфавита, чтобы не попасть
    в топ ни по одной наивной сортировке) несёт тег "Сыры". Старая версия
    (`candidates_for_taste`/`search`, пул 30) могла НЕ увидеть его вовсе —
    новая обязана, потому что видит все 40."""
    cards = [_card(f"filler-{i:02d}", winery=f"winery-{i}") for i in range(39)]
    cards.append(_card("zzz-the-one", winery="target-winery", food_pairings=["Сыры"]))
    _patch_catalog(monkeypatch, cards)

    wines = select_wines_for_dish(object(), "Сыры", _settings())

    assert any(w["wine_id"] == "zzz-the-one" for w in wines), (
        "вино с тегом категории обязано попасть в выдачу независимо от позиции в каталоге"
    )
    top = next(w for w in wines if w["wine_id"] == "zzz-the-one")
    assert top["basis"] == "catalog"


# --------------------------------------------------------------------------
# Ярус catalog — включение ТОЛЬКО по тегу, сортировка по score движка правил
# --------------------------------------------------------------------------

def test_catalog_tier_includes_all_tag_matches_sorted_by_engine_score(monkeypatch):
    """Три вина с тегом "BBQ", разный sensory -> разный score движка правил
    (не просто алфавит) — порядок внутри яруса catalog обязан идти по score."""
    mid_bbq = {  # score 0.526 (sверено прогоном score_wine_for_dish) — ниже _BBQ_SENSORY (0.737), но > 0
        "sweetness": 0.1, "acidity": 0.5, "tannin": 0.6, "body": 0.7,
        "oak": 0.3, "aromatic_intensity": 0.6, "bubbles": 0.0,
    }
    cards = [
        _card("bbq-strong", winery="w1", food_pairings=["BBQ"], sensory=_BBQ_SENSORY),
        _card("bbq-mid", winery="w2", food_pairings=["BBQ"], sensory=mid_bbq),
        _card("bbq-no-vector", winery="w3", food_pairings=["BBQ"], color="", sensory=None),
    ]
    _patch_catalog(monkeypatch, cards)

    wines = select_wines_for_dish(object(), "BBQ", _settings())

    assert [w["wine_id"] for w in wines] == ["bbq-strong", "bbq-mid", "bbq-no-vector"]
    assert all(w["basis"] == "catalog" for w in wines)
    assert all(w["reason"] == "Портал рекомендует это вино к категории «BBQ»." for w in wines)


def test_catalog_tier_includes_tagged_wine_even_when_hard_blocked(monkeypatch):
    """dry_wine_with_dessert (food_pairing_rules.yaml hard_blocks) обычно
    исключает тег целиком (см. GET /wines/{id}/pairings) — но здесь портал
    УЖЕ сказал "сочетается" через food_pairings, включение по тегу не
    отменяется хард-блоком, только теряет приоритет в сортировке (score=0)."""
    dry_sensory = {
        "sweetness": 0.05, "acidity": 0.6, "tannin": 0.1, "body": 0.6,
        "oak": 0.3, "aromatic_intensity": 0.7, "bubbles": 0.0,
    }
    cards = [_card("dry-but-tagged", winery="w1", food_pairings=["Выпечка и десерты"], sensory=dry_sensory)]
    _patch_catalog(monkeypatch, cards)

    wines = select_wines_for_dish(object(), "Выпечка и десерты", _settings())

    assert [w["wine_id"] for w in wines] == ["dry-but-tagged"]
    assert wines[0]["basis"] == "catalog"


# --------------------------------------------------------------------------
# Ярус rules — только положительный score, hard-block исключает целиком
# --------------------------------------------------------------------------

def test_rules_tier_fills_remaining_slots_with_positive_score_reason(monkeypatch):
    cards = [_card("bbq-fan", winery="w1", sensory=_BBQ_SENSORY)]
    _patch_catalog(monkeypatch, cards)

    wines = select_wines_for_dish(object(), "BBQ", _settings())

    assert len(wines) == 1
    assert wines[0]["basis"] == "rules"
    assert wines[0]["reason"]


def test_rules_tier_hard_block_excludes_wine_entirely(monkeypatch):
    dry_sensory = {
        "sweetness": 0.05, "acidity": 0.6, "tannin": 0.1, "body": 0.6,
        "oak": 0.3, "aromatic_intensity": 0.7, "bubbles": 0.0,
    }
    cards = [_card("bone-dry", winery="w1", sensory=dry_sensory)]  # НЕ помечено тегом
    _patch_catalog(monkeypatch, cards)

    assert select_wines_for_dish(object(), "Выпечка и десерты", _settings()) == []


def test_wine_without_usable_vector_in_rules_tier_is_skipped_not_crashed(monkeypatch):
    cards = [_card("no-signal", winery="w1", color="", sensory=None)]
    _patch_catalog(monkeypatch, cards)
    assert select_wines_for_dish(object(), "BBQ", _settings()) == []


# --------------------------------------------------------------------------
# Диверсификация, детерминизм, пустой каталог
# --------------------------------------------------------------------------

def test_at_most_six_wines_and_max_two_per_winery(monkeypatch):
    cards = [_card(f"bbq-{i}", winery="dense-winery", sensory=_BBQ_SENSORY) for i in range(5)]
    cards += [_card(f"other-{i}", winery=f"winery-{i}", sensory=_BBQ_SENSORY) for i in range(5)]
    _patch_catalog(monkeypatch, cards)

    wines = select_wines_for_dish(object(), "BBQ", _settings())

    assert len(wines) <= 6
    wineries = [w["winery"] for w in wines]
    assert all(wineries.count(w) <= 2 for w in wineries)
    assert wineries.count("Dense-Winery") == 2


def test_deterministic_across_repeated_calls(monkeypatch):
    cards = [_card("a", winery="w1", food_pairings=["Сыры"]), _card("b", winery="w2", sensory=_BBQ_SENSORY)]
    _patch_catalog(monkeypatch, cards)
    r1 = select_wines_for_dish(object(), "Сыры", _settings())
    r2 = select_wines_for_dish(object(), "Сыры", _settings())
    assert r1 == r2


def test_empty_catalog_returns_empty_list_not_exception(monkeypatch):
    _patch_catalog(monkeypatch, [])
    assert select_wines_for_dish(object(), "Сыры", _settings()) == []


# --------------------------------------------------------------------------
# Кэш "один раз на процесс" (`_build_catalog_cards`, `@lru_cache` по retriever)
# --------------------------------------------------------------------------

def test_build_catalog_cards_cached_once_per_retriever_object(monkeypatch):
    calls: list[int] = []

    def fake_all_slugs():
        calls.append(1)
        return ["w1"]

    monkeypatch.setattr(dish_pairing.case_catalog, "all_slugs", fake_all_slugs)
    monkeypatch.setattr(
        dish_pairing, "build_wine_card",
        lambda retriever, slug: {"source": {"name": slug, "food_pairings": []}, "derived": {}},
    )

    retriever_a = object()
    retriever_b = object()
    dish_pairing._build_catalog_cards(retriever_a)
    dish_pairing._build_catalog_cards(retriever_a)  # тот же retriever -> кэш, не второй вызов
    assert len(calls) == 1

    dish_pairing._build_catalog_cards(retriever_b)  # другой retriever -> отдельная запись
    assert len(calls) == 2


# --------------------------------------------------------------------------
# warm_up_catalog_cache() — тимлид 22.09: "холодный кэш 12 с не должен
# доставаться первому пользователю... прогревай при старте, в фоновом
# потоке". Фоновый поток и интеграция с create_app() — tests/test_main.py;
# здесь — сама функция прогрева в изоляции.
# --------------------------------------------------------------------------

def test_warm_up_catalog_cache_builds_cache_and_returns_true(monkeypatch):
    monkeypatch.setattr(dish_pairing.case_catalog, "all_slugs", lambda: ["w1"])
    monkeypatch.setattr(
        dish_pairing, "build_wine_card",
        lambda retriever, slug: {"source": {"name": slug, "food_pairings": []}, "derived": {}},
    )
    retriever = object()

    assert dish_pairing.warm_up_catalog_cache(retriever) is True
    assert dish_pairing._build_catalog_cards(retriever) == (("w1", {"name": "w1", "food_pairings": []}, None),)


def test_warm_up_catalog_cache_failure_returns_false_not_raises(monkeypatch):
    def boom():
        raise RuntimeError("CASE_DATA_DIR недоступен")

    monkeypatch.setattr(dish_pairing.case_catalog, "all_slugs", boom)
    assert dish_pairing.warm_up_catalog_cache(object()) is False
