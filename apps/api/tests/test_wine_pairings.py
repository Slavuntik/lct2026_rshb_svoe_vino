"""GET /v1/wines/{wine_id}/pairings — contracts/post-scan.md v1.0 §1,
contracts/openapi.yaml 0.3.3 (22.09, "Задача backend" из
reports/architect-post-scan.md). Паттерн — tests/test_wines.py
(register_user/auth_header/client.get, `_write_case_catalog` для фолбэка
каталога кейса).

Все ожидаемые числа ниже сверены НЕ вручную, а прогоном настоящего
`app/food_pairing.py` поверх настоящего `pipeline/ref/food_pairing_rules.yaml`
(файл только читаем, не меняем) — см. reports/backend-pairings.md, "Как
проверить".
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from tests.conftest import auth_header, register_user


def _write_case_catalog(tmp_path, mapping: dict) -> None:
    (tmp_path / "case_catalog.json").write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False), encoding="utf-8"
    )


def _pairing_tags(body: dict) -> list[str]:
    return [item["tag"] for item in body["pairings"]]


# --- Уровень 1 — basis=catalog (наш каталог, source.food_pairings как есть) -

def test_pairings_catalog_basis_returns_food_pairings_as_is(client: TestClient):
    """shato-vymysel-cabernet — фикстура с непустым food_pairings
    (app/rag/fixtures.py) — уровень 1 побеждает без обращения к движку правил
    вовсе: score всегда null, triggered_rules всегда []."""
    tokens = register_user(client, email="pair-catalog@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wine_id"] == "shato-vymysel-cabernet"
    assert body["basis"] == "catalog"
    assert body["message"] is None
    assert body["pairings"] == [
        {"tag": "Мясо и стейки", "score": None, "triggered_rules": []},
        {"tag": "Твёрдые сыры", "score": None, "triggered_rules": []},
    ]


# --- Уровень 2 — basis=sensory (derived.sensory через движок правил) -------

def test_pairings_sensory_basis_scores_via_rules_engine(client: TestClient, monkeypatch):
    """food_pairings опустошён ПРЯМО в фикстуре (monkeypatch.setitem — тот же
    приём, что tests/test_scan_photo.py, откатывается сам после теста) — вино
    при этом несёт derived.sensory (фикстура не меняется), значит уровень 2."""
    from app.rag.fixtures import WINES_BY_SLUG

    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "food_pairings", [])

    tokens = register_user(client, email="pair-sensory@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "sensory"
    assert body["message"] is None
    assert len(body["pairings"]) <= 3
    assert _pairing_tags(body) == ["Блюда из птицы", "BBQ", "Сыры"]
    for item in body["pairings"]:
        assert 0 < item["score"] <= 1
        assert item["triggered_rules"], "score>0 обязан нести хотя бы одно triggered_rule"
    assert body["pairings"][0]["score"] == pytest.approx(1.0)
    assert body["pairings"][1]["score"] == pytest.approx(0.5263)
    assert body["pairings"][2]["score"] == pytest.approx(0.2857)


# --- Регресс qa-auto (reports/qa-auto-post-scan.md, 22.09): битые записи ------
# derived.sensory каталога — ключи с null вместо отсутствующих ключей.
# chateau-de-talu-uroki-frantsuzskogo-krasnostop-krasnostop-anapskiy-krasnoe-
# suhoe-145 (1 из 1978) ронял 500 (`float(None)`, food_pairing.py:324 на тот
# момент) — ниже НЕ должно 500-ить ни при каких пропусках осей, а вместо
# basis=sensory обязана честно деградировать (вектор с дырой не может
# скориться надёжно — см. docstring _is_usable_sensory).

def test_pairings_sensory_with_null_axis_degrades_to_heuristic_not_500(client: TestClient, monkeypatch):
    """Ровно класс дефекта qa-auto: derived.sensory непуст, но 3 из 7 осей —
    ключи со значением null (не отсутствуют, а именно null). shato-vymysel-
    cabernet несёт color="красное" — есть куда деградировать (heuristic),
    так что basis обязан смениться, а не остаться sensory с дырой в векторе."""
    from app.rag.fixtures import WINES_BY_SLUG

    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "food_pairings", [])
    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "derived", {
        "sensory": {
            "sweetness": None, "acidity": None, "tannin": 0.62, "body": 0.72,
            "oak": 0.38, "aromatic_intensity": None, "bubbles": 0.0,
        },
    })

    tokens = register_user(client, email="pair-null-axis@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "heuristic"  # НЕ "sensory" — вектор с null-осью ненадёжен
    assert body["message"] is None
    assert _pairing_tags(body) == ["BBQ", "Блюда из птицы", "Сыры"]


def test_pairings_fully_null_sensory_degrades_to_unavailable_not_500(client: TestClient, monkeypatch):
    """"Полностью пустой" sensory — ВСЕ 7 осей null (не просто отсутствующий
    словарь {} — тот и так уже работал, см. каталог кейса ниже). color тоже
    убран, чтобы дойти до самого дна каскада — доказывает, что деградация
    работает до basis=unavailable включительно, а не спотыкается на полпути."""
    from app.rag.fixtures import WINES_BY_SLUG

    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "food_pairings", [])
    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "color", "")
    monkeypatch.setitem(WINES_BY_SLUG["shato-vymysel-cabernet"], "derived", {
        "sensory": {axis: None for axis in
                    ("sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles")},
    })

    tokens = register_user(client, email="pair-null-sensory-full@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "unavailable"
    assert body["pairings"] == []
    assert body["message"]


# --- Уровень 3 — basis=heuristic (каталог кейса: ни food_pairings, ни sensory) -

def test_pairings_heuristic_basis_from_case_catalog_color(client: TestClient, tmp_path, monkeypatch):
    """Каталог кейса НИКОГДА не несёт food_pairings/derived.sensory
    (app/rag/cards.py::build_wine_card, фолбэк) — обычный случай при
    сканировании (contracts/post-scan.md §1). name/description намеренно без
    ключевых слов сахара/игристости — чистая проверка таблицы дефолтов
    (color=Белое/тихое)."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-default": {
            "name": "Погребок №5", "winery_name": "Тестовая винодельня",
            "region_name": "Кубань", "grapes": ["Рислинг"], "color": "Белое",
            "category": "Соломенный",
            "description": "Вино из тестовой партии для проверки API.",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="pair-heuristic@example.com")
    r = client.get("/v1/wines/case-heuristic-default/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "heuristic"
    assert body["message"] is None
    assert _pairing_tags(body) == ["Блюда из рыбы", "Сыры", "Выпечка и десерты"]
    assert body["pairings"][0]["score"] == pytest.approx(1.0)
    assert body["pairings"][1]["score"] == pytest.approx(0.8)
    assert body["pairings"][2]["score"] == pytest.approx(0.6061)


def test_pairings_heuristic_keyword_brut_hard_blocks_dessert_pairing(client: TestClient, tmp_path, monkeypatch):
    """contracts/post-scan.md §1: "«брют» -> sweetness=0.05 независимо от
    дефолта цвета" — то же color=Белое, что в тесте дефолта выше (дефолт дал
    бы sweetness=0.20 и «Выпечка и десерты» в топ-3), но имя с «брют» роняет
    sweetness до 0.05 — hard_blocks.dry_wine_with_dessert (pipeline/ref/
    food_pairing_rules.yaml) исключает «Выпечка и десерты» ЦЕЛИКОМ, даже
    несмотря на то, что без блока это был бы топ-3 (score 0.6061) — тот самый
    "хотя бы 1 сценарий на hard_blocks" из брифа."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-brut": {
            "name": "Резерв Брют", "winery_name": "Тестовая винодельня",
            "region_name": "Кубань", "grapes": ["Шардоне"], "color": "Белое",
            "category": "Соломенный", "description": "",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="pair-brut@example.com")
    r = client.get("/v1/wines/case-heuristic-brut/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "heuristic"
    tags = _pairing_tags(body)
    assert "Выпечка и десерты" not in tags, "dry_wine_with_dessert обязан исключить тег целиком"
    assert tags == ["Блюда из рыбы", "Сыры", "Блюда из птицы"]


def test_pairings_heuristic_keyword_sparkling_sets_bubbles_axis(client: TestClient, tmp_path, monkeypatch):
    """contracts/post-scan.md §1: "«игристое» -> строка bubbles игристого
    подрежима". С дефолтным (тихим) вектором «Брускетты» не попадает в топ-3
    вовсе (score 0.3462, ниже «Выпечка и десерты» 0.6061, см. тест дефолта
    выше) — bubbles=0.73 игристой строки включает salt_loves_bubbles и
    поднимает «Брускетты» до топ-3, доказывая, что ключевое слово реально
    поменяло ось bubbles, а не просто сахар."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-sparkling": {
            "name": "Игристое чудо", "winery_name": "Тестовая винодельня",
            "region_name": "Дон", "grapes": ["Шардоне"], "color": "Белое",
            "category": "Светло-золотистый", "description": "",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="pair-sparkling@example.com")
    r = client.get("/v1/wines/case-heuristic-sparkling/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "heuristic"
    assert "Брускетты" in _pairing_tags(body)


def test_pairings_alphabetical_tiebreak_orders_equal_scores_by_tag(client: TestClient, tmp_path, monkeypatch):
    """Тот же игристый вектор, что и тест ключевого слова выше, даёт РОВНО
    три тега со score=1.0 (Блюда из рыбы / Брускетты / Сыры — сверено прогоном
    настоящего движка, reports/backend-pairings.md) — top_n=3 забирает их всех
    целиком, порядок решает ТОЛЬКО алфавит тега (contracts/post-scan.md §1,
    "Правила скоринга", п.4): "Блюда из рыбы" < "Брускетты" (л < р) <
    "Сыры" (С после Б)."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-tiebreak": {
            "name": "Игристое чудо", "winery_name": "Тестовая винодельня",
            "region_name": "Дон", "grapes": ["Шардоне"], "color": "Белое",
            "category": "Светло-золотистый", "description": "",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="pair-tiebreak@example.com")
    r = client.get("/v1/wines/case-heuristic-tiebreak/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert _pairing_tags(body) == ["Блюда из рыбы", "Брускетты", "Сыры"]
    assert all(item["score"] == pytest.approx(1.0) for item in body["pairings"])


# --- Уровень 4 — basis=unavailable (даже color пуст) ------------------------

def test_pairings_unavailable_when_color_empty(client: TestClient, tmp_path, monkeypatch):
    """Та же фикстура каталога кейса, что и в тестах heuristic выше, но
    color="" — самая частая честная деградация ("в дампе кейса это редкие
    строки без колонки «Категория»", contracts/post-scan.md §1)."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-unavailable": {
            "name": "Вино без карточки цвета", "winery_name": "Тестовая винодельня",
            "region_name": "Кубань", "grapes": [], "color": "",
            "category": "", "description": "",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="pair-unavailable@example.com")
    r = client.get("/v1/wines/case-heuristic-unavailable/pairings", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "unavailable"
    assert body["pairings"] == []
    assert body["message"]  # непусто ⟺ pairings=[]


# --- 404 и детерминизм -------------------------------------------------------

def test_unknown_wine_id_pairings_is_404_not_found(client: TestClient, tmp_path, monkeypatch):
    """CASE_DATA_DIR указывает на пустую директорию — тот же приём, что
    tests/test_wines.py::test_unknown_wine_id_still_404_when_case_catalog_also_does_not_know_it,
    не полагаемся на реальные данные кейса на машине."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    tokens = register_user(client, email="pair-404@example.com")

    r = client.get("/v1/wines/does-not-exist-anywhere-at-all/pairings", headers=auth_header(tokens))

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_pairings_requires_auth(client: TestClient):
    """Тот же принцип, что GET /wines/{id} (test_wines.py) — гастропары
    висят на той же карточке, тот же Bearer-гейт."""
    r = client.get("/v1/wines/shato-vymysel-cabernet/pairings")
    assert r.status_code == 401


def test_pairings_deterministic_across_repeated_calls(client: TestClient, tmp_path, monkeypatch):
    """contracts/post-scan.md — никакого LLM, никакой случайности: два
    вызова подряд обязаны дать байт-в-байт идентичный ответ."""
    _write_case_catalog(tmp_path, {
        "case-heuristic-determinism": {
            "name": "Погребок №5", "winery_name": "Тестовая винодельня",
            "region_name": "Кубань", "grapes": ["Рислинг"], "color": "Белое",
            "category": "Соломенный",
            "description": "Вино из тестовой партии для проверки API.",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    tokens = register_user(client, email="pair-determinism@example.com")

    r1 = client.get("/v1/wines/case-heuristic-determinism/pairings", headers=auth_header(tokens))
    r2 = client.get("/v1/wines/case-heuristic-determinism/pairings", headers=auth_header(tokens))

    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
