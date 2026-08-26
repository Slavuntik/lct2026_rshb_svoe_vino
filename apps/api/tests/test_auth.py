from __future__ import annotations

from starlette.testclient import TestClient

from app.main import create_app
from app.models import ConsentLedger, User
from app.ratelimit import reset_rate_limits
from tests.conftest import auth_header, make_guest, register_user


def test_register_under_18_is_403_age_restricted(client: TestClient):
    r = client.post("/v1/auth/register", json={
        "email": "kid@example.com", "password": "password123", "birth_date": "2015-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    })
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "age_restricted"


def test_register_exactly_18_today_is_allowed(client: TestClient):
    from datetime import date
    today = date.today()
    birth_date = today.replace(year=today.year - 18)
    r = client.post("/v1/auth/register", json={
        "email": "just18@example.com", "password": "password123",
        "birth_date": birth_date.isoformat(), "consent_version": "v1", "consent_scopes": ["base"],
    })
    assert r.status_code == 201, r.text


def test_register_without_base_scope_is_validation_error(client: TestClient):
    r = client.post("/v1/auth/register", json={
        "email": "nobase@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "v1", "consent_scopes": ["marketing"],
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_register_writes_one_ledger_row_per_scope(client: TestClient, app):
    register_user(client, email="ledger@example.com", scopes=["base", "profiling", "marketing"])

    session_factory = app.state.session_factory
    with session_factory() as db:
        user = db.query(User).filter(User.email == "ledger@example.com").one()
        rows = db.query(ConsentLedger).filter(ConsentLedger.user_id == user.id).all()
        scopes = {row.scope for row in rows}
        assert scopes == {"base", "profiling", "marketing"}
        assert all(row.granted for row in rows)
        assert all(row.consent_version == "2026-01-01.1" for row in rows)
        # sha256(ip+соль суток), не сырой IP (contracts/schema.sql комментарий)
        assert all(row.ip_hash and len(row.ip_hash) == 64 for row in rows)


def test_register_duplicate_email_rejected(client: TestClient):
    register_user(client, email="dup@example.com")
    r = client.post("/v1/auth/register", json={
        "email": "dup@example.com", "password": "password123", "birth_date": "1990-01-01",
        "consent_version": "v1", "consent_scopes": ["base"],
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_login_success_returns_jwt(client: TestClient):
    register_user(client, email="login@example.com", password="correcthorse")
    r = client.post("/v1/auth/login", json={"email": "login@example.com", "password": "correcthorse"})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_login_wrong_password_is_invalid_credentials(client: TestClient):
    register_user(client, email="wrongpw@example.com", password="correcthorse")
    r = client.post("/v1/auth/login", json={"email": "wrongpw@example.com", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_login_unknown_email_is_invalid_credentials_not_404(client: TestClient):
    r = client.post("/v1/auth/login", json={"email": "ghost@example.com", "password": "whatever1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_jwt_actually_authorizes_protected_endpoint(client: TestClient):
    tokens = register_user(client, email="protected@example.com")
    r = client.get("/v1/consents", headers=auth_header(tokens))
    assert r.status_code == 200


def test_missing_token_is_401_unauthorized(client: TestClient):
    r = client.get("/v1/consents")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_garbage_token_is_401_unauthorized(client: TestClient):
    r = client.get("/v1/consents", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


# --- /auth/guest (v0.2) --------------------------------------------------

def test_guest_age_not_confirmed_is_403_age_restricted(client: TestClient):
    r = client.post("/v1/auth/guest", json={"age_confirmed": False, "consent_version": "v1"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "age_restricted"


def test_guest_confirmed_gets_token_and_base_consent_ledger_row(client: TestClient, app):
    tokens = make_guest(client)
    assert tokens["access_token"]

    import jwt as pyjwt
    payload = pyjwt.decode(tokens["access_token"], options={"verify_signature": False})
    # v0.2.1: JWT больше не несёт "kind" — оно вычисляется из users.is_guest
    # на каждый запрос (см. app/security.py::_kind_of), а не фиксируется в
    # токене на момент выдачи (важно для апгрейда: старый токен той же
    # строки должен "поумнеть" сразу после регистрации, без переиздания).
    assert "kind" not in payload
    guest_id = payload["sub"]

    with app.state.session_factory() as db:
        rows = db.query(ConsentLedger).filter(ConsentLedger.user_id == guest_id).all()
        assert len(rows) == 1
        assert rows[0].scope == "base"
        assert rows[0].granted is True
        # v0.2.1: у гостя ЕСТЬ полноценная строка в users (is_guest=True,
        # email/password_hash/birth_date NULL) — именно это чинит FK у
        # scans/chat_messages/events/feedback (см. test_guest_attribution.py).
        guest_row = db.get(User, guest_id)
        assert guest_row is not None
        assert guest_row.is_guest is True
        assert guest_row.email is None
        assert guest_row.password_hash is None
        assert guest_row.birth_date is None
        assert guest_row.age_confirmed_at is not None


def test_guest_can_reach_scan_but_not_swipes(client: TestClient):
    tokens = make_guest(client)
    headers = auth_header(tokens)

    r = client.post("/v1/scan/resolve", json={"text": "Шато Вымысел"}, headers=headers)
    assert r.status_code == 200

    r = client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"}, headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"


def test_guest_cannot_export_or_delete_profile(client: TestClient):
    headers = auth_header(make_guest(client))
    r = client.get("/v1/profile/data-export", headers=headers)
    assert r.status_code == 401
    r = client.delete("/v1/profile", headers=headers)
    assert r.status_code == 401


# --- rate limit -----------------------------------------------------------

def test_auth_endpoints_are_rate_limited(monkeypatch):
    # Отдельное приложение с низким лимитом вместо фикстуры client/app:
    # Settings — frozen dataclass, зафиксированный в app.state на момент
    # create_app(), поэтому лимит нужно выставить в env ДО его вызова.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "2")
    reset_rate_limits()
    client = TestClient(create_app())

    for _ in range(2):
        r = client.post("/v1/auth/login", json={"email": "nope@example.com", "password": "x"})
        assert r.status_code == 401  # ещё в пределах лимита, просто неверные креды

    r = client.post("/v1/auth/login", json={"email": "nope@example.com", "password": "x"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
