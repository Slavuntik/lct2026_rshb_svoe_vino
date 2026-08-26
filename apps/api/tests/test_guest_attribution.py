"""v0.2.1: гость — полноценная строка users (NULL-identity), поэтому FK у
scans/chat_messages/events/feedback теперь работают и на него — раньше (v0.2)
эти колонки приходилось обнулять для гостя (fk_user_id), чтобы не поймать
IntegrityError на несуществующем id.

Второй, отдельный вопрос: SQLite по умолчанию МОЛЧА игнорирует FK, если не
включить `PRAGMA foreign_keys=ON` на каждое соединение (app/db.py). Без этой
проверки тесты ниже могли бы "проходить" и на сломанном коде — а расхождение
всплыло бы только на Postgres в проде, в худшем случае прямо на демо. Тесты
в конце файла целенаправленно нарушают FK напрямую через ORM, в обход
роутеров, чтобы доказать, что constraint в этом dev-движке реально активен.
"""
from __future__ import annotations

import jwt as pyjwt
import pytest
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

from app.models import ChatMessage, ConsentLedger, Event, Feedback, Scan
from tests.conftest import auth_header, make_guest


def _sub(access_token: str) -> str:
    return pyjwt.decode(access_token, options={"verify_signature": False})["sub"]


def test_guest_scan_is_attributed_to_the_guests_own_user_id(client: TestClient, app):
    tokens = make_guest(client)
    guest_id = _sub(tokens["access_token"])
    client.post("/v1/scan/resolve", json={"text": "Игристое Небо Брют"}, headers=auth_header(tokens))

    with app.state.session_factory() as db:
        row = db.query(Scan).filter(Scan.user_id == guest_id).one()
        assert row.query_text == "Игристое Небо Брют"


def test_guest_chat_messages_are_attributed_to_the_guests_own_user_id(client: TestClient, app):
    tokens = make_guest(client)
    guest_id = _sub(tokens["access_token"])
    client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))

    with app.state.session_factory() as db:
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == guest_id).all()
        assert {r.role for r in rows} == {"user", "assistant"}


def test_guest_event_is_attributed_to_the_guests_own_user_id(client: TestClient, app):
    tokens = make_guest(client)
    guest_id = _sub(tokens["access_token"])
    r = client.post("/v1/events", json={"name": "scan_started", "props": {"mode": "text"}},
                     headers=auth_header(tokens))
    assert r.status_code == 204

    with app.state.session_factory() as db:
        row = db.query(Event).filter(Event.name == "scan_started").one()
        assert row.user_id == guest_id


def test_guest_feedback_is_attributed_to_the_guests_own_user_id(client: TestClient, app):
    import json as jsonlib
    tokens = make_guest(client)
    guest_id = _sub(tokens["access_token"])
    headers = auth_header(tokens)

    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=headers)
    answer_id = None
    for block in r.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                evt = jsonlib.loads(line[len("data:"):].strip())
                if evt["type"] == "done":
                    answer_id = evt["answer_id"]
    assert answer_id is not None

    r = client.post("/v1/chat/feedback", json={"answer_id": answer_id, "verdict": "up"}, headers=headers)
    assert r.status_code == 204

    with app.state.session_factory() as db:
        row = db.query(Feedback).filter(Feedback.message_id == int(answer_id)).one()
        assert row.user_id == guest_id


# --- PRAGMA foreign_keys=ON: доказать, что constraint реально активен ------

def test_sqlite_fk_pragma_rejects_scan_with_nonexistent_user_id(app):
    """Если бы PRAGMA foreign_keys=ON не был включён (app/db.py), эта вставка
    молча прошла бы на SQLite и разошлась бы с Postgres в проде."""
    with app.state.session_factory() as db:
        db.add(Scan(user_id="00000000-0000-0000-0000-000000000000", query_text="orphan"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_sqlite_fk_pragma_rejects_chat_message_with_nonexistent_user_id(app):
    with app.state.session_factory() as db:
        db.add(ChatMessage(user_id="00000000-0000-0000-0000-000000000000", role="user", content="x"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_consent_ledger_still_has_no_fk_and_accepts_any_user_id(app):
    """Контраст с тестами выше: consent_ledger осознанно БЕЗ FK (v0.2, блокер
    1) — обязан пережить удаление строки users. Это НЕ баг, а единственная
    таблица, где произвольный user_id — норма."""
    with app.state.session_factory() as db:
        db.add(ConsentLedger(
            user_id="11111111-1111-1111-1111-111111111111",
            consent_version="v1", scope="base", granted=True,
        ))
        db.commit()  # не должно поднять IntegrityError
