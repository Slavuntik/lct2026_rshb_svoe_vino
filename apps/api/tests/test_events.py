from __future__ import annotations

from starlette.testclient import TestClient

from app.models import Event
from tests.conftest import auth_header, register_user


def test_valid_event_name_is_204_and_persisted(client: TestClient, app):
    r = client.post("/v1/events", json={"name": "app_open", "props": {"platform": "web"}})
    assert r.status_code == 204

    with app.state.session_factory() as db:
        rows = db.query(Event).filter(Event.name == "app_open").all()
        assert len(rows) == 1
        assert rows[0].props["platform"] == "web"
        assert rows[0].props["_v"] == "0.1"  # проставляется сервером, если клиент не передал
        assert rows[0].user_id is None  # анонимное событие, без токена


def test_unknown_event_name_is_400(client: TestClient):
    r = client.post("/v1/events", json={"name": "totally_made_up_event", "props": {}})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_event"


def test_event_with_authenticated_user_records_user_id(client: TestClient, app):
    tokens = register_user(client, email="ev1@example.com")
    r = client.post("/v1/events", json={"name": "onboarding_completed", "props": {"scopes": ["base"]}},
                     headers=auth_header(tokens))
    assert r.status_code == 204
    with app.state.session_factory() as db:
        row = db.query(Event).filter(Event.name == "onboarding_completed").one()
        assert row.user_id is not None


def test_event_props_rejects_pii_like_keys(client: TestClient):
    r = client.post("/v1/events", json={
        "name": "waitlist_joined", "props": {"email": "leak@example.com"},
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_event_props_rejects_long_free_text_values(client: TestClient):
    r = client.post("/v1/events", json={
        "name": "app_open", "props": {"note": "x" * 500},
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_all_events_dictionary_names_are_accepted(client: TestClient):
    from app.events_dict import EVENT_NAMES
    for name in EVENT_NAMES:
        r = client.post("/v1/events", json={"name": name, "props": {}})
        assert r.status_code == 204, f"{name} should be accepted, got {r.status_code}: {r.text}"
