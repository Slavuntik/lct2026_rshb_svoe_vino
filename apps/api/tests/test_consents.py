from __future__ import annotations

from starlette.testclient import TestClient

from app.models import User
from tests.conftest import auth_header, register_user


def test_get_consents_reflects_registration_scopes(client: TestClient):
    tokens = register_user(client, email="c1@example.com", scopes=["base", "marketing"])
    r = client.get("/v1/consents", headers=auth_header(tokens))
    assert r.status_code == 200
    by_scope = {row["scope"]: row for row in r.json()}
    assert by_scope["base"]["granted"] is True
    assert by_scope["marketing"]["granted"] is True
    assert "profiling" not in by_scope


def test_post_consents_grant_new_scope(client: TestClient):
    tokens = register_user(client, email="c2@example.com", scopes=["base"])
    r = client.post("/v1/consents", json={
        "consent_version": "2026-02-01.1", "grant": True, "scopes": ["marketing"],
    }, headers=auth_header(tokens))
    assert r.status_code == 204

    r = client.get("/v1/consents", headers=auth_header(tokens))
    by_scope = {row["scope"]: row for row in r.json()}
    assert by_scope["marketing"]["granted"] is True
    assert by_scope["marketing"]["consent_version"] == "2026-02-01.1"


def test_get_consents_returns_latest_state_after_revoke(client: TestClient):
    tokens = register_user(client, email="c3@example.com", scopes=["base", "marketing"])
    client.post("/v1/consents", json={
        "consent_version": "2026-02-01.1", "grant": False, "scopes": ["marketing"],
    }, headers=auth_header(tokens))

    r = client.get("/v1/consents", headers=auth_header(tokens))
    by_scope = {row["scope"]: row for row in r.json()}
    assert by_scope["marketing"]["granted"] is False


def test_revoking_base_via_consents_soft_deletes_registered_user(client: TestClient, app):
    tokens = register_user(client, email="c4@example.com", scopes=["base"])
    r = client.post("/v1/consents", json={
        "consent_version": "2026-02-01.1", "grant": False, "scopes": ["base"],
    }, headers=auth_header(tokens))
    assert r.status_code == 204

    with app.state.session_factory() as db:
        user = db.query(User).filter(User.email == "c4@example.com").one()
        assert user.deleted_at is not None

    r = client.post("/v1/auth/login", json={"email": "c4@example.com", "password": "password123"})
    assert r.status_code == 401


def test_guest_can_use_consents_without_a_users_row(client: TestClient):
    from tests.conftest import make_guest
    tokens = make_guest(client)
    r = client.get("/v1/consents", headers=auth_header(tokens))
    assert r.status_code == 200
    assert any(row["scope"] == "base" for row in r.json())
