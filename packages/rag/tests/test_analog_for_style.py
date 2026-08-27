"""analog_for_style('prosecco') возвращает только игристые.

Юнит-уровень: rag.styles.StyleMatcher на синтетических payload'ах (без
ingest/Qdrant/эмбеддингов — analog_for_style устроен как чисто метаданный
запрос, см. докстринг модуля). Плюс сквозной тест через Retriever на
мини-фикстуре с настоящим декоем (красное игристое с другим стилем).
"""
from __future__ import annotations

from rag.styles import StyleMatcher
from rag.types import Filters

_PROSECCO_STYLE = {
    "slug": "prosecco",
    "name": "Просекко",
    "country": "Италия",
    "color": "белое",
    "sugar": ["брют", "экстра брют", "сухое"],
    "stillness": "игристое",
    "sensory": {"acidity": 0.65, "body": 0.4, "sweetness": 0.25, "bubbles": 0.7, "oak": 0.0},
}

_BORDEAUX_STYLE = {
    "slug": "bordeaux-right-bank",
    "name": "Бордо правый берег",
    "country": "Франция",
    "color": "красное",
    "sugar": ["сухое"],
    "stillness": "тихое",
    "sensory": {"acidity": 0.55, "body": 0.75, "tannin": 0.7, "sweetness": 0.05, "bubbles": 0.0},
}


def _payload(pid, color, stillness, sugar, styles, sensory):
    return {
        "id": pid,
        "kind": "wine",
        "text": f"{pid} текст",
        "url": f"https://example.invalid/{pid}",
        # "filters"/"sensory" — внутреннее представление (фильтрация/ранжирование).
        "filters": {"color": color, "stillness": stillness, "sugar": sugar, "reference_style_matches": styles},
        "sensory": sensory,
        # "source"/"derived" — то, что реально уходит в Candidate.meta (контракт
        # v0.3, ревью 02, блокер 1): kind=wine -> {"source":{...},"derived":{...}}.
        "source": {"color": color, "sugar_category": sugar},
        "derived": {"stillness": stillness, "reference_style_matches": styles},
    }


_WINES = [
    _payload("sparkling-white-1", "белое", "игристое", "брют", ["prosecco"], {"acidity": 0.6, "bubbles": 0.65}),
    _payload("sparkling-white-2", "белое", "игристое", "экстра брют", ["prosecco"], {"acidity": 0.7, "bubbles": 0.8}),
    # декой: тоже отмечен style-матчером под другой стиль — не должен утечь в prosecco
    _payload("red-still-1", "красное", "тихое", "сухое", ["bordeaux-right-bank"], {"tannin": 0.7}),
    # защитный декой: НЕ должен утечь, даже если бы (гипотетически) кто-то
    # ошибочно приписал ему prosecco в апстримных данных, а цвет/тихость не сходятся
    _payload("mismatched-color-1", "красное", "игристое", "брют", ["prosecco"], {"acidity": 0.6}),
]


def test_analog_for_style_only_sparkling():
    sm = StyleMatcher(styles=[_PROSECCO_STYLE, _BORDEAUX_STYLE])
    results = sm.analog_for_style("prosecco", _WINES, top_k=12)

    ids = [c.id for c in results]
    assert "sparkling-white-1" in ids
    assert "sparkling-white-2" in ids
    assert "red-still-1" not in ids
    # цвет расходится с эталоном стиля — защитный фильтр должен исключить,
    # даже несмотря на style-матч в данных (см. styles.py: перепроверка цвета/stillness/сахара)
    assert "mismatched-color-1" not in ids

    for c in results:
        assert c.kind == "wine"
        assert c.meta["derived"]["stillness"] == "игристое"
        assert c.meta["source"]["color"] == "белое"


def test_analog_for_style_unknown_slug_returns_empty():
    sm = StyleMatcher(styles=[_PROSECCO_STYLE])
    assert sm.analog_for_style("no-such-style-xyz", _WINES) == []


def test_analog_for_style_respects_extra_user_filters():
    sm = StyleMatcher(styles=[_PROSECCO_STYLE])
    only_prosecco = [w for w in _WINES if "prosecco" in w["filters"]["reference_style_matches"]]
    # доп. фильтр по сахару, которому удовлетворяет только один из двух prosecco-кандидатов
    results = sm.analog_for_style("prosecco", only_prosecco, filters=Filters(sugar="брют"))
    ids = [c.id for c in results]
    assert ids == ["sparkling-white-1"]


def test_analog_for_style_on_real_reference_styles():
    # Без синтетики: настоящий ref/reference_styles.yaml (read-only), тот же
    # набор кандидатов, что и в проверке фильтров/декоя выше.
    sm = StyleMatcher()  # грузит реальный справочник
    results = sm.analog_for_style("prosecco", _WINES, top_k=12)
    ids = [c.id for c in results]
    assert "sparkling-white-1" in ids
    assert "red-still-1" not in ids
    assert "mismatched-color-1" not in ids


def test_analog_for_style_end_to_end_on_tiny_index(tiny_index):
    results = tiny_index.analog_for_style("prosecco", top_k=8)
    assert results
    ids = [c.id for c in results]
    assert "sparkling-white-prosecco-1" in ids
    assert "sparkling-red-decoy-1" not in ids  # другой style-тег, не должен утечь
    for c in results:
        assert c.meta["derived"]["stillness"] == "игристое"
