from __future__ import annotations

import json

from starlette.testclient import TestClient

from app.chat.service import run_chat
from app.rag.interface import Candidate
from tests.conftest import auth_header, make_guest, register_user


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class _EmptyRetriever:
    """Стаб: пустая выдача независимо от запроса — для юнит-теста refusal
    напрямую на app.chat.service.run_chat, без завязки на подбор порогов
    fuzzy-скоринга в MockRetriever."""

    index_version = "stub-empty"

    def search(self, query, *, filters=None, collections=("wines", "knowledge"), top_k=8):
        return []


class _RecordingLLM:
    """Оборачивает реальный (mock) LLM и запоминает все messages, с которыми
    его вызвали — чтобы проверить, что туда не попал email/id пользователя."""

    def __init__(self, inner):
        self.inner = inner
        self.seen_messages: list[list[dict]] = []

    def chat(self, messages, **kw):
        self.seen_messages.append(messages)
        return self.inner.chat(messages, **kw)

    def chat_stream(self, messages, **kw):
        self.seen_messages.append(messages)
        yield from self.inner.chat_stream(messages, **kw)


# --- SSE happy path ---------------------------------------------------------

def test_chat_sse_emits_tokens_then_citations_then_done(client: TestClient):
    tokens = register_user(client, email="chat1@example.com")
    r = client.post("/v1/chat", json={"message": "Что подать к стейку из говядины?"},
                     headers=auth_header(tokens))
    assert r.status_code == 200
    events = parse_sse(r.text)
    types = [e["type"] for e in events]

    assert types[-1] == "done"
    assert "citation" in types
    first_citation_idx = types.index("citation")
    last_token_idx = max(i for i, t in enumerate(types) if t == "token")
    assert last_token_idx < first_citation_idx, "все token должны идти до первой citation"
    assert first_citation_idx < types.index("done"), "citation должна быть раньше done"
    assert types.count("token") >= 1


def test_chat_citations_carry_ordinal_n_matching_answer_markers(client: TestClient):
    tokens = register_user(client, email="chat2@example.com")
    r = client.post("/v1/chat", json={"message": "Хочу лёгкое красное вино"},
                     headers=auth_header(tokens))
    events = parse_sse(r.text)
    full_text = "".join(e["text"] for e in events if e["type"] == "token")
    citations = [e for e in events if e["type"] == "citation"]

    assert citations, "должна быть хотя бы одна цитата"
    for c in citations:
        assert "n" in c and isinstance(c["n"], int)
        assert f"[{c['n']}]" in full_text
        assert ("wine_id" in c) ^ ("chunk_id" in c), "ровно одно из wine_id/chunk_id"
        assert c["url"].startswith("https://")
        assert c["quote"]


def test_chat_requires_auth(client: TestClient):
    r = client.post("/v1/chat", json={"message": "Привет"})
    assert r.status_code == 401


def test_chat_works_for_guest(client: TestClient):
    tokens = make_guest(client)
    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))
    assert r.status_code == 200
    assert parse_sse(r.text)[-1]["type"] == "done"


def test_chat_persists_user_and_assistant_messages(client: TestClient, app):
    tokens = register_user(client, email="chat3@example.com")
    client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))
    with app.state.session_factory() as db:
        from app.models import ChatMessage
        rows = db.query(ChatMessage).order_by(ChatMessage.at).all()
        assert [r.role for r in rows] == ["user", "assistant"]
        assert rows[0].content == "Что подать к стейку?"
        assert len(rows[1].citations) >= 1
        assert rows[1].trace["index_version"]


# --- refusal on empty retrieval ---------------------------------------------

def test_run_chat_refuses_on_empty_retrieval(monkeypatch):
    from llm.drivers.mock import MockLLM

    outcome = run_chat(message="что угодно", retriever=_EmptyRetriever(), llm=MockLLM())
    assert outcome.kind == "refusal"
    assert outcome.reason
    assert outcome.citations == []


def test_chat_endpoint_refuses_end_to_end_on_gibberish(client: TestClient):
    tokens = register_user(client, email="chat4@example.com")
    gibberish = "asdkfj qpwoeiru zxcvbnm 000111 blah blah qqzz"
    r = client.post("/v1/chat", json={"message": gibberish}, headers=auth_header(tokens))
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert events == [{"type": "refusal", "reason": events[0]["reason"]}]
    assert "citation" not in [e["type"] for e in events]


# --- privacy: email/id must never reach the LLM -----------------------------

def test_email_never_reaches_llm_prompt(client: TestClient, app):
    """agents/B-api.md: "тест «в промпт не утёк email» (прогнать сборку
    промпта на юзере с данными)". Регистрируем пользователя с характерным
    email, даём ему вкусовой профиль (свайп), спрашиваем чат и проверяем,
    что ни email, ни id пользователя не встречаются НИ В ОДНОМ сообщении,
    которое реально ушло в LLM.
    """
    recorder = _RecordingLLM(app.state.llm)
    app.state.llm = recorder

    distinctive_email = "vyacheslav.superprivate.owner@example.com"
    tokens = register_user(client, email=distinctive_email, scopes=["base", "profiling"])
    headers = auth_header(tokens)

    client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                headers=headers)

    from app.models import User
    with app.state.session_factory() as db:
        user_id = db.query(User).filter(User.email == distinctive_email).one().id

    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=headers)
    assert r.status_code == 200

    assert recorder.seen_messages, "LLM должен был быть вызван — иначе тест ничего не проверяет"
    for messages in recorder.seen_messages:
        for msg in messages:
            content = msg["content"]
            assert distinctive_email not in content, "email утёк в промпт LLM"
            assert user_id not in content, "id пользователя утёк в промпт LLM"
            assert "@example.com" not in content, "похоже, в промпт утекла почта"


def test_format_taste_vector_has_no_identity_fields():
    from app.chat.prompt import format_taste_vector

    out = format_taste_vector({"sweetness": 0.3, "acidity": 0.7, "tannin": 0.1, "body": 0.5,
                                "oak": 0.0, "aromatic_intensity": 0.4, "bubbles": 0.0})
    assert out == "sweetness=0.30 acidity=0.70 tannin=0.10 body=0.50 oak=0.00 aromatic_intensity=0.40 bubbles=0.00"


def test_build_messages_signature_has_no_identity_parameters():
    """Структурная гарантия: build_messages физически не принимает email/id —
    их невозможно случайно туда добавить, не поменяв сигнатуру."""
    import inspect

    from app.chat.prompt import build_messages

    params = set(inspect.signature(build_messages).parameters)
    assert params == {"message", "candidates", "filters", "taste_summary"}


# --- /chat/feedback ----------------------------------------------------------

def test_chat_feedback_happy_path(client: TestClient, app):
    tokens = register_user(client, email="fb1@example.com")
    headers = auth_header(tokens)
    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=headers)
    answer_id = parse_sse(r.text)[-1]["answer_id"]

    r = client.post("/v1/chat/feedback", json={"answer_id": answer_id, "verdict": "up"}, headers=headers)
    assert r.status_code == 204

    with app.state.session_factory() as db:
        from app.models import Feedback
        row = db.query(Feedback).filter(Feedback.message_id == int(answer_id)).one()
        assert row.verdict == "up"


def test_chat_feedback_unknown_answer_id_is_404(client: TestClient):
    tokens = register_user(client, email="fb2@example.com")
    r = client.post("/v1/chat/feedback", json={"answer_id": "999999", "verdict": "down"},
                     headers=auth_header(tokens))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_chat_feedback_non_numeric_answer_id_is_404_not_500(client: TestClient):
    tokens = register_user(client, email="fb3@example.com")
    r = client.post("/v1/chat/feedback", json={"answer_id": "not-a-number", "verdict": "up"},
                     headers=auth_header(tokens))
    assert r.status_code == 404
