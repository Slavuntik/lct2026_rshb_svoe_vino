from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import auth_header, register_user


def test_wine_card_shape_matches_contract(client: TestClient):
    tokens = register_user(client, email="w1@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert set(["wine_id", "source", "derived", "source_url"]).issubset(body.keys())
    assert body["wine_id"] == "shato-vymysel-cabernet"
    assert body["source"]["name"] == "Шато Вымысел Каберне Совиньон"
    assert "sensory" in body["derived"]
    assert body["source_url"].startswith("https://")
    # source не должен дублировать derived/slug/source_url внутри себя
    assert "derived" not in body["source"]
    assert "source_url" not in body["source"]


def test_wine_card_includes_similar(client: TestClient):
    tokens = register_user(client, email="w2@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    assert "tihaya-gavan-pinot-noir" in r.json()["similar"]


def test_unknown_wine_id_is_404_not_found(client: TestClient):
    tokens = register_user(client, email="w3@example.com")
    r = client.get("/v1/wines/does-not-exist-at-all", headers=auth_header(tokens))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_wine_card_requires_auth(client: TestClient):
    r = client.get("/v1/wines/shato-vymysel-cabernet")
    assert r.status_code == 401
