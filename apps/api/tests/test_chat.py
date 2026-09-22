from __future__ import annotations

import json

from starlette.testclient import TestClient

from app.chat.service import stream_chat_events
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
    напрямую на app.chat.service.stream_chat_events, без завязки на подбор
    порогов fuzzy-скоринга в MockRetriever."""

    index_version = "stub-empty"

    def search(self, query, *, filters=None, collections=None, top_k=8):
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


def test_chat_falls_back_to_prompt_citations_when_llm_omits_markers(client: TestClient, app):
    """v0.3.2 (флаг B, следствие настоящего стриминга): если генерация
    закончилась БЕЗ единого [n] в тексте, сервер обязан перед done отправить
    citation-события по ВСЕМ выдержкам, реально попавшим в промпт —
    непривязанные к тексту, но восстанавливающие гарантию "каждый ответ
    несёт источники" по построению (контекст промпта и есть источник)."""

    class _NoMarkersLLM:
        def chat(self, messages, **kw):
            return "Ответ без единой ссылки на источник в тексте."

        def chat_stream(self, messages, **kw):
            yield "Ответ "
            yield "без "
            yield "единой "
            yield "ссылки "
            yield "на источник в тексте."

    app.state.llm = _NoMarkersLLM()

    tokens = register_user(client, email="nomarkers1@example.com")
    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))
    assert r.status_code == 200

    events = parse_sse(r.text)
    types = [e["type"] for e in events]
    full_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "[" not in full_text, "тест именно про случай без маркеров в тексте"

    citations = [e for e in events if e["type"] == "citation"]
    assert citations, "v0.3.2: цитаты обязаны прийти, даже если модель не расставила [n]"
    assert [c["n"] for c in citations] == list(range(1, len(citations) + 1)), (
        "нумерация n продолжается 1..N по выдержкам промпта, без дыр"
    )
    for c in citations:
        assert ("wine_id" in c) ^ ("chunk_id" in c)
        assert c["quote"]

    assert types.index("citation") < types.index("done"), "citation обязана прийти до done"
    assert types[-1] == "done"

    # Кросс-проверка через персистентность: цитат ровно столько же, сколько
    # выдержек реально ушло в промпт (trace.candidate_ids) — не часть, не с потолка.
    with app.state.session_factory() as db:
        from app.models import ChatMessage
        assistant_msg = (
            db.query(ChatMessage)
            .filter(ChatMessage.role == "assistant")
            .order_by(ChatMessage.at.desc())
            .first()
        )
        assert len(assistant_msg.citations) == len(assistant_msg.trace["candidate_ids"])
        assert len(assistant_msg.citations) == len(citations)


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


# --- v0.3: настоящий стриминг, не буферизация -------------------------------
#
# TestClient.stream() в этой среде НЕ отражает реальную потоковую доставку по
# сети даже для тривиального StreamingResponse с time.sleep() между yield —
# проверено отдельным диагностическим скриптом на голом FastAPI-эндпоинте:
# все server-side yield успевают отработать ДО того, как клиент получает
# первую строку, независимо от того, насколько ленив код сервера. Значит,
# HTTP-уровневый тест "первый token раньше последнего чанка LLM по времени"
# бьётся о транспорт теста, а не о реальное поведение. Вместо этого — прямое,
# детерминированное, независимое от транспорта доказательство на уровне
# генератора: stream_chat_events() не должен вытягивать llm.chat_stream()
# целиком (list(...)) до первого token-события.

def test_stream_chat_events_pulls_llm_chunks_one_at_a_time_not_eagerly():
    """contracts/openapi.yaml v0.3: "буферизация полного ответа запрещена".
    Если бы stream_chat_events() делал `list(llm.chat_stream(...))` (как до
    этой правки), то уже после ПЕРВОГО next() генератора у LLM оказались бы
    вытянуты ВСЕ чанки. Здесь после первого next() у LLM должен быть
    запрошен ровно один."""
    from app.rag.mock import MockRetriever

    chunks = ["[1] ", "раз ", "два ", "три ", "четыре."]
    pulled: list[str] = []

    class _CountingLLM:
        def chat(self, messages, **kw):
            return "".join(chunks)

        def chat_stream(self, messages, **kw):
            for chunk in chunks:
                pulled.append(chunk)
                yield chunk

    gen = stream_chat_events(
        message="Что подать к стейку?", retriever=MockRetriever(), llm=_CountingLLM(),
    )

    first_event = next(gen)
    assert first_event == {"type": "token", "text": chunks[0]}
    assert pulled == [chunks[0]], (
        f"после первого next() LLM должен был отдать ровно 1 чанк, а отдал "
        f"{len(pulled)} — похоже на eager-материализацию (list(...)), "
        f"запрещённую v0.3"
    )

    second_event = next(gen)
    assert second_event == {"type": "token", "text": chunks[1]}
    assert pulled == chunks[:2], "второй next() должен вытянуть ровно ещё один чанк"

    # Дочитываем до конца, чтобы не оставлять генератор недопотреблённым.
    remaining = list(gen)
    assert pulled == chunks  # к концу — все чанки, но не раньше своей очереди
    assert remaining[-1]["type"] == "_ready"


def test_chat_sse_over_http_delivers_tokens_in_llm_yield_order(client: TestClient, app):
    """HTTP-уровневый регресс на КОРРЕКТНОСТЬ порядка после сборки (не на
    тайминг доставки, см. коммент выше): дошедшие до клиента token-события
    идут в том же порядке, что и у LLM, без потерь и перестановок."""
    chunks = ["Раз ", "два ", "три ", "[1] ", "готово."]

    class _OrderedLLM:
        def chat(self, messages, **kw):
            return "".join(chunks)

        def chat_stream(self, messages, **kw):
            yield from chunks

    app.state.llm = _OrderedLLM()

    tokens = register_user(client, email="stream2@example.com")
    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))
    events = parse_sse(r.text)
    received = [e["text"] for e in events if e["type"] == "token"]

    assert received == chunks


# --- v0.3.4 (22.09, reports/backend-chat-retrieval.md, п.3): бюджет длины ответа ---

def test_chat_passes_configured_max_tokens_to_llm_chat_stream(client: TestClient, app):
    """Первый прогон стенда без лимита ушёл на 497 токенов / ~84 с до конца
    (первый токен — приемлемые 4.5 с, но долгое молчание ПОСЛЕ него плохо
    смотрится на демо). /v1/chat обязан передавать settings.chat_max_tokens
    в llm.chat_stream(), не полагаться на driver default в Protocol LLM
    (llm/base.py, 1024)."""
    captured: list[dict] = []

    class _CapturingLLM:
        def chat(self, messages, **kw):
            return "ответ [1]"

        def chat_stream(self, messages, **kw):
            captured.append(kw)
            yield "ответ [1]"

    app.state.llm = _CapturingLLM()

    tokens = register_user(client, email="maxtok1@example.com")
    r = client.post("/v1/chat", json={"message": "Что подать к стейку?"}, headers=auth_header(tokens))
    assert r.status_code == 200

    assert captured, "chat_stream должен был быть вызван ровно один раз"
    assert captured[0].get("max_tokens") == app.state.settings.chat_max_tokens


def test_default_chat_max_tokens_setting_is_450():
    from app.config import Settings

    assert Settings().chat_max_tokens == 450


def test_stream_chat_events_forwards_max_tokens_to_chat_stream():
    """Юнит-уровень (без HTTP): stream_chat_events() принимает max_tokens и
    пробрасывает его в llm.chat_stream() как есть; дефолт 1024 (тот же, что у
    Protocol LLM) сохраняет старое поведение для прямых вызывающих (тесты),
    которые не передают его вовсе."""
    from app.rag.mock import MockRetriever

    captured: list[dict] = []

    class _CapturingLLM:
        def chat_stream(self, messages, **kw):
            captured.append(kw)
            yield "[1] ответ"

    list(stream_chat_events(
        message="Что подать к стейку?", retriever=MockRetriever(), llm=_CapturingLLM(), max_tokens=450,
    ))
    assert captured[0]["max_tokens"] == 450

    captured.clear()
    list(stream_chat_events(message="Что подать к стейку?", retriever=MockRetriever(), llm=_CapturingLLM()))
    assert captured[0]["max_tokens"] == 1024, "дефолт без явного max_tokens не должен был измениться"


# --- v0.3.3: collections не форсируется — intent-роутинг ретривера жив -------

def test_stream_chat_events_calls_search_without_forcing_collections():
    """contracts/rag-interface.md v0.3.3 (ревью 03, блокер заморозки):
    collections=None у Retriever.search() — дефолт и норма, включает
    intent-роутинг настоящего ретривера (pairing-запросы приоритезируют
    wines над knowledge). Явный tuple форсирует набор коллекций и молча
    обходит этот роутинг — живым зондом ревьюера доказано, что именно так
    сцена 2 демо через реальный API начинала цитировать статьи знаний
    вместо вин на wine-pick вопросы. Шпион на аргументах: chat/service.py
    не имеет права передавать collections вовсе."""
    from app.rag.mock import MockRetriever
    from llm.drivers.mock import MockLLM

    calls: list[tuple[tuple, dict]] = []
    retriever = MockRetriever()
    original_search = retriever.search
    retriever.search = lambda *a, **kw: (calls.append((a, kw)), original_search(*a, **kw))[1]

    list(stream_chat_events(message="Что подать к стейку?", retriever=retriever, llm=MockLLM()))

    assert calls, "search должен был быть вызван ровно один раз"
    _args, kwargs = calls[0]
    assert "collections" not in kwargs, (
        "collections не должен передаваться явно — это форсирует набор "
        "коллекций и обходит intent-роутинг ретривера (v0.3.3)"
    )


# --- refusal on empty retrieval ---------------------------------------------

def test_stream_chat_events_refuses_on_empty_retrieval_without_calling_llm():
    from llm.drivers.mock import MockLLM

    llm = MockLLM()
    events = list(stream_chat_events(message="что угодно", retriever=_EmptyRetriever(), llm=llm))
    assert events == [{"type": "refusal", "reason": events[0]["reason"]}]
    assert events[0]["reason"]


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


def test_system_prompt_refuses_off_topic_questions_not_just_insufficient_excerpts():
    """Ревью 03 (митигация refusal-риска): второй барьер к отсечке
    ретривера — если вопрос вообще не о вине/еде, промпт обязан требовать
    вежливый отказ, а не попытку ответить из нерелевантных выдержек, которые
    всё же прошли порог ретривера."""
    from app.chat.prompt import SYSTEM_PROMPT

    assert "не о вине" in SYSTEM_PROMPT or "не про вино" in SYSTEM_PROMPT
    assert "откажись" in SYSTEM_PROMPT


def test_system_prompt_is_honest_about_not_filtering_by_price():
    """reports/backend-chat-retrieval.md (22.09, п.2): в каталоге кейса
    (strapi_output0709.csv) поля цены нет вовсе — ни одна выдержка её не
    несёт, ретривер по цене не фильтрует. Промпт обязан требовать честность
    об этом, а не позволять модели молчать или выдумывать бюджет/цифру."""
    from app.chat.prompt import SYSTEM_PROMPT

    assert "цен" in SYSTEM_PROMPT.lower()  # "цену"/"цены"/"ценам" и т.п.
    assert "не выдумывай стоимость" in SYSTEM_PROMPT or "не фильтруешь" in SYSTEM_PROMPT


def test_system_prompt_asks_for_short_bounded_wine_recommendations():
    """reports/backend-chat-retrieval.md (22.09, п.3): вместе с max_tokens
    (settings.chat_max_tokens) промпт просит модель САМУ уложиться в короткий
    формат, а не полагаться только на жёсткую обрезку посреди слова."""
    from app.chat.prompt import SYSTEM_PROMPT

    assert "3 вин" in SYSTEM_PROMPT
    assert "[n]" in SYSTEM_PROMPT


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
