from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import auth_header, make_guest, register_user


def test_scan_resolve_requires_auth(client: TestClient):
    r = client.post("/v1/scan/resolve", json={"text": "Шато Вымысел Каберне"})
    assert r.status_code == 401


def test_scan_resolve_contract_shape_on_good_match(client: TestClient):
    tokens = register_user(client, email="scan1@example.com")
    r = client.post("/v1/scan/resolve", json={"text": "Шато Вымысел Каберне Совиньон"},
                     headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert "matches" in body and "low_confidence" in body
    assert len(body["matches"]) <= 5
    top = body["matches"][0]
    assert set(["wine_id", "name", "winery_name", "confidence"]).issubset(top.keys())
    assert top["wine_id"] == "shato-vymysel-cabernet"
    assert 0 <= top["confidence"] <= 1
    assert body["low_confidence"] is False


def test_scan_resolve_low_confidence_on_garbage_text(client: TestClient):
    tokens = register_user(client, email="scan2@example.com")
    r = client.post("/v1/scan/resolve", json={"text": "zzz qqq garbage не вино вообще 12345"},
                     headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["low_confidence"] is True


def test_scan_resolve_hints_boost_matching_color(client: TestClient):
    tokens = register_user(client, email="scan3@example.com")
    r = client.post("/v1/scan/resolve", json={
        "text": "Мускат Закат", "hints": {"color": "белое"},
    }, headers=auth_header(tokens))
    assert r.status_code == 200
    assert r.json()["matches"][0]["wine_id"] == "sladkiy-zakat-muskat"


def test_scan_ocr_is_stubbed_501_not_implemented(client: TestClient):
    tokens = register_user(client, email="scan4@example.com")
    r = client.post(
        "/v1/scan/ocr",
        data={"explicit_consent": "true"},
        files={"image": ("label.jpg", b"\xff\xd8\xff", "image/jpeg")},
        headers=auth_header(tokens),
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "not_implemented"


def test_scan_resolve_works_for_guest_too(client: TestClient):
    tokens = make_guest(client)
    r = client.post("/v1/scan/resolve", json={"text": "Розовый Мираж"}, headers=auth_header(tokens))
    assert r.status_code == 200
