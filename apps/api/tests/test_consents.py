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


def test_post_consents_unknown_scope_is_400_validation_error(client: TestClient, app):
    """v0.3: enum обязателен к валидации — неизвестный скоуп => 400
    validation_error, в ledger попадают только словарные значения."""
    tokens = register_user(client, email="c5@example.com", scopes=["base"])
    r = client.post("/v1/consents", json={
        "consent_version": "v1", "grant": True, "scopes": ["base", "not_a_real_scope"],
    }, headers=auth_header(tokens))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"

    from app.models import ConsentLedger, User
    with app.state.session_factory() as db:
        user = db.query(User).filter(User.email == "c5@example.com").one()
        rows = db.query(ConsentLedger).filter(ConsentLedger.user_id == user.id).all()
        scopes_written = {r.scope for r in rows}
        assert "not_a_real_scope" not in scopes_written
        # запрос отклонён целиком — второй "base" из этого же вызова тоже не попал в ledger
        assert len([r for r in rows if r.scope == "base"]) == 1  # только из регистрации


def test_post_consents_all_dictionary_scopes_are_accepted(client: TestClient):
    tokens = register_user(client, email="c6@example.com", scopes=["base"])
    r = client.post("/v1/consents", json={
        "consent_version": "v1", "grant": True, "scopes": ["base", "profiling", "geo", "marketing"],
    }, headers=auth_header(tokens))
    assert r.status_code == 204


def test_guest_can_use_consents(client: TestClient):
    """v0.2.1: гость — полноценная строка users, но /consents работал для
    него и раньше (v0.2, когда строки не было вовсе) — ledger никогда не
    зависел от FK на users. Имя теста подправлено, поведение то же."""
    from tests.conftest import make_guest
    tokens = make_guest(client)
    r = client.get("/v1/consents", headers=auth_header(tokens))
    assert r.status_code == 200
    assert any(row["scope"] == "base" for row in r.json())
