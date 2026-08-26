from __future__ import annotations

from starlette.testclient import TestClient

from app.models import ConsentLedger
from tests.conftest import auth_header, register_user


def test_data_export_contains_everything_the_user_generated(client: TestClient):
    tokens = register_user(client, email="exp1@example.com", scopes=["base", "profiling"])
    headers = auth_header(tokens)

    client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                headers=headers)
    client.post("/v1/scan/resolve", json={"text": "Розовый Мираж"}, headers=headers)
    client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=headers)

    r = client.get("/v1/profile/data-export", headers=headers)
    assert r.status_code == 200
    body = r.json()

    assert body["user"]["email"] == "exp1@example.com"
    assert "password_hash" not in body["user"]  # секрет аутентификации, не персональные данные к выгрузке
    assert len(body["consents"]) >= 2  # base + profiling
    assert len(body["swipes"]) == 1
    assert body["taste_profile"] is not None
    assert len(body["scans"]) == 1
    assert len(body["chat_messages"]) == 2  # user + assistant
    assert len(body["events"]) == 0  # сервер сам не пишет events за клиента (см. routers/events.py)


def test_data_export_requires_registered_user(client: TestClient):
    r = client.get("/v1/profile/data-export")
    assert r.status_code == 401


def test_delete_profile_then_relogin_is_401(client: TestClient):
    tokens = register_user(client, email="del1@example.com", password="deleteme123")
    r = client.delete("/v1/profile", headers=auth_header(tokens))
    assert r.status_code == 204

    r = client.post("/v1/auth/login", json={"email": "del1@example.com", "password": "deleteme123"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_delete_profile_token_itself_stops_working(client: TestClient):
    tokens = register_user(client, email="del2@example.com")
    headers = auth_header(tokens)
    client.delete("/v1/profile", headers=headers)
    r = client.get("/v1/consents", headers=headers)
    assert r.status_code == 401


def test_consent_ledger_survives_profile_deletion(client: TestClient, app):
    """v0.2 (ревью 01, блокер 1): consent_ledger больше не имеет FK на users
    именно для того, чтобы пережить удаление аккаунта — доказуемость отзыва
    нужна ровно после того, как пользователь удалился."""
    tokens = register_user(client, email="del3@example.com", scopes=["base", "profiling"])

    with app.state.session_factory() as db:
        from app.models import User
        user_id = db.query(User).filter(User.email == "del3@example.com").one().id

    client.delete("/v1/profile", headers=auth_header(tokens))

    with app.state.session_factory() as db:
        rows = db.query(ConsentLedger).filter(ConsentLedger.user_id == user_id).all()
        assert len(rows) > 0, "ledger не должен быть удалён вместе с аккаунтом"
        scopes_granted_false = {r.scope for r in rows if not r.granted}
        assert "base" in scopes_granted_false, "отзыв base должен быть записан явно"
