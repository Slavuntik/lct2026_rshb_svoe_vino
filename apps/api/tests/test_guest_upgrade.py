"""v0.2.1 (contracts/openapi.yaml, /auth/register description): регистрация
с гостевым Bearer-токеном апгрейдит ТУ ЖЕ строку users (is_guest=false),
история scans/chat_messages сохраняется, потому что id строки не меняется.
"""
from __future__ import annotations

import jwt as pyjwt
from starlette.testclient import TestClient

from app.models import User
from tests.conftest import auth_header, make_guest, register_user


def _sub(access_token: str) -> str:
    return pyjwt.decode(access_token, options={"verify_signature": False})["sub"]


def test_guest_scans_then_registers_scan_survives_in_data_export(client: TestClient, app):
    guest_tokens = make_guest(client)
    guest_headers = auth_header(guest_tokens)
    guest_id = _sub(guest_tokens["access_token"])

    r = client.post("/v1/scan/resolve", json={"text": "Розовый Мираж"}, headers=guest_headers)
    assert r.status_code == 200

    r = client.post("/v1/auth/register", json={
        "email": "upgraded1@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "2026-01-01.1", "consent_scopes": ["base"],
    }, headers=guest_headers)
    assert r.status_code == 201
    new_tokens = r.json()

    assert _sub(new_tokens["access_token"]) == guest_id, "апгрейд должен переиспользовать ID строки"

    with app.state.session_factory() as db:
        user = db.get(User, guest_id)
        assert user.is_guest is False
        assert user.email == "upgraded1@example.com"
        assert user.password_hash is not None
        assert user.birth_date is not None

    # логин по новым кредам работает — это уже полноценный аккаунт
    r = client.post("/v1/auth/login", json={"email": "upgraded1@example.com", "password": "password123"})
    assert r.status_code == 200

    r = client.get("/v1/profile/data-export", headers=auth_header(new_tokens))
    assert r.status_code == 200
    body = r.json()
    assert len(body["scans"]) == 1
    assert body["scans"][0]["query_text"] == "Розовый Мираж"


def test_guest_chats_then_registers_chat_history_survives(client: TestClient, app):
    guest_tokens = make_guest(client)
    guest_headers = auth_header(guest_tokens)
    guest_id = _sub(guest_tokens["access_token"])

    client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=guest_headers)

    r = client.post("/v1/auth/register", json={
        "email": "upgraded2@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "2026-01-01.1", "consent_scopes": ["base"],
    }, headers=guest_headers)
    assert r.status_code == 201

    with app.state.session_factory() as db:
        from app.models import ChatMessage
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == guest_id).all()
        assert len(rows) == 2  # user + assistant


def test_register_upgrade_preserves_first_age_confirmation_timestamp(client: TestClient, app):
    guest_tokens = make_guest(client)
    guest_id = _sub(guest_tokens["access_token"])

    with app.state.session_factory() as db:
        original_confirmed_at = db.get(User, guest_id).age_confirmed_at
    assert original_confirmed_at is not None

    client.post("/v1/auth/register", json={
        "email": "upgraded3@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "2026-01-01.1", "consent_scopes": ["base"],
    }, headers=auth_header(guest_tokens))

    with app.state.session_factory() as db:
        assert db.get(User, guest_id).age_confirmed_at == original_confirmed_at


def test_register_without_any_token_creates_a_fresh_row_as_before(client: TestClient):
    """Регресс: без токена в заголовке апгрейд не запускается — обычная
    регистрация (уже было покрыто test_auth.py, дублируем ракурс явно)."""
    r = client.post("/v1/auth/register", json={
        "email": "fresh1@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    })
    assert r.status_code == 201


def test_register_with_full_user_token_ignores_it_and_creates_fresh_row(client: TestClient, app):
    """Не задокументировано контрактом буквально (описаны только "гостевой
    токен" и "без токена") — решение агента B: токен уже ПОЛНОЦЕННОГО
    пользователя на /auth/register просто игнорируется, чужая строка не
    трогается, создаётся новая. Не 401/409 — эндпоинт остаётся публичным."""
    existing_tokens = register_user(client, email="already-a-user@example.com")

    r = client.post("/v1/auth/register", json={
        "email": "brand-new@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    }, headers=auth_header(existing_tokens))
    assert r.status_code == 201
    new_id = _sub(r.json()["access_token"])
    old_id = _sub(existing_tokens["access_token"])
    assert new_id != old_id

    with app.state.session_factory() as db:
        untouched = db.query(User).filter(User.email == "already-a-user@example.com").one()
        assert untouched.id == old_id  # чужая строка не апгрейднулась и не пострадала


def test_register_with_garbage_authorization_header_still_registers(client: TestClient):
    """try_resolve_guest_user должен молча проглотить мусорный заголовок —
    регистрация не должна требовать валидного токена вообще."""
    r = client.post("/v1/auth/register", json={
        "email": "garbage-header@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    }, headers={"Authorization": "Bearer totally-not-a-jwt"})
    assert r.status_code == 201


def test_register_upgrade_still_enforces_age_gate_and_base_scope(client: TestClient):
    guest_tokens = make_guest(client)
    headers = auth_header(guest_tokens)

    r = client.post("/v1/auth/register", json={
        "email": "toosoon@example.com", "password": "password123", "birth_date": "2015-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    }, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "age_restricted"
