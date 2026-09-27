from tests.conftest import make_guest, auth_header
from app import dish_pairing


def cards():
    return (
        (
            "white-dry",
            {
                "name": "Белое сухое",
                "color": "белое",
                "sugar_category": "сухое",
                "region": "kuban",
                "food_pairings": ["Блюда из рыбы"],
            },
            None,
        ),
        (
            "white-sweet",
            {
                "name": "Белое сладкое",
                "color": "белое",
                "sugar_category": "сладкое",
                "region": "kuban",
                "food_pairings": ["Блюда из рыбы"],
            },
            None,
        ),
        (
            "red-dry",
            {
                "name": "Красное сухое",
                "color": "красное",
                "sugar_category": "сухое",
                "region": "kuban",
                "food_pairings": ["Мясо и стейки"],
            },
            None,
        ),
    )


def test_rank_only_recognized_catalog_wines(client, monkeypatch):
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda *args: cards())
    headers = auth_header(make_guest(client))
    r = client.post(
        "/v1/sommelier/shelf-selection",
        headers=headers,
        json={
            "wish": "белое сухое",
            "dish": "рыба",
            "wine_ids": ["white-dry", "white-sweet", "invented"],
        },
    )
    assert r.status_code == 200, r.text
    assert [w["wine_id"] for w in r.json()["wines"]] == ["white-dry"]
    assert r.json()["wines"][0]["basis"] == "catalog-pairing"
    assert r.json()["wines"][0]["rank"] == 1


def test_empty_scan_never_recommends_absent_wines(client, monkeypatch):
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda *args: cards())
    headers = auth_header(make_guest(client))
    r = client.post(
        "/v1/sommelier/shelf-selection",
        headers=headers,
        json={"wish": "белое", "wine_ids": []},
    )
    assert r.status_code == 200 and r.json()["wines"] == []


def test_negated_sugar_not_positive_filter_and_budget_not_invented(client, monkeypatch):
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda *args: cards())
    headers = auth_header(make_guest(client))
    r = client.post(
        "/v1/sommelier/shelf-selection",
        headers=headers,
        json={"wish": "белое не сладкое до 1000 рублей"},
    )
    assert r.status_code == 200
    assert [w["wine_id"] for w in r.json()["wines"]] == ["white-dry"]
    assert any("ценник" in w for w in r.json()["warnings"])


def test_shelf_selection_requires_guest_and_limits_inputs(client):
    assert (
        client.post("/v1/sommelier/shelf-selection", json={"wish": "белое"}).status_code
        == 401
    )
    headers = auth_header(make_guest(client))
    assert (
        client.post(
            "/v1/sommelier/shelf-selection",
            headers=headers,
            json={"wish": "белое", "wine_ids": ["x"] * 641},
        ).status_code
        == 422
    )


def test_wish_infers_food_and_normalizes_catalog_case(client, monkeypatch):
    data = list(cards())
    data[0][1]["color"] = "Белое"
    data[0][1]["sugar_category"] = "Сухое"
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda *args: data)
    response = client.post(
        "/v1/sommelier/shelf-selection",
        headers=auth_header(make_guest(client)),
        json={
            "wish": "сухое белое к рыбе, не сладкое",
            "wine_ids": ["white-dry", "white-sweet"],
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["understood"] == ["белое", "сухое", "Блюда из рыбы", "Без: сладкое"]
    assert result["wines"][0]["basis"] == "catalog-pairing"
    assert [w["wine_id"] for w in result["wines"]] == ["white-dry"]


def test_long_identifiers_rejected(client):
    response = client.post(
        "/v1/sommelier/shelf-selection",
        headers=auth_header(make_guest(client)),
        json={"wish": "белое", "wine_ids": ["a" * 301]},
    )
    assert response.status_code == 422
