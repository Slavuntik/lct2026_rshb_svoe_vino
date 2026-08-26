from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import auth_header, register_user


def test_analogs_resolves_style_and_returns_wines(client: TestClient):
    tokens = register_user(client, email="an1@example.com")
    r = client.post("/v1/analogs", json={"query": "люблю Просекко"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["style"]["slug"] == "prosecco"
    assert len(body["wines"]) >= 1
    assert body["wines"][0]["wine_id"] == "igristoe-nebo-brut"
    assert len(body["wines"]) <= 12


def test_analogs_unrecognized_style_is_404_with_top5_hint(client: TestClient):
    tokens = register_user(client, email="an2@example.com")
    r = client.post("/v1/analogs", json={"query": "совершенно неразборчивый набор слов xyz"},
                     headers=auth_header(tokens))
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    assert "Просекко" in body["error"]["message"] or "прос" in body["error"]["message"].lower()


def test_analogs_filters_by_region(client: TestClient):
    tokens = register_user(client, email="an3@example.com")
    r = client.post("/v1/analogs", json={
        "query": "что-то как Вальполичелла", "filters": {"region": "kuban"},
    }, headers=auth_header(tokens))
    assert r.status_code == 200
    for wine in r.json()["wines"]:
        assert wine["region_name"] == "Кубань"


def test_analogs_requires_auth(client: TestClient):
    r = client.post("/v1/analogs", json={"query": "просекко"})
    assert r.status_code == 401
