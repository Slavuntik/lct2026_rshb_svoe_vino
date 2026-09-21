"""Драйверы deepseek/gigachat/anthropic: проверяем сборку запроса и разбор
ответа ОФФЛАЙН через httpx.MockTransport. Реальная сеть не вызывается —
это требование проекта (в тестах внешние LLM-API не звать).
"""
import json

import httpx
import pytest

from llm.base import LLMUnavailable
from llm.drivers.anthropic import AnthropicLLM
from llm.drivers.deepseek import DeepSeekLLM
from llm.drivers.gigachat import GigaChatLLM

MESSAGES = [
    {"role": "system", "content": "Ты сомелье."},
    {"role": "user", "content": "Что подать к стейку?"},
]


def _sse(*payloads: dict, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in payloads]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------

def test_deepseek_chat_parses_openai_shaped_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-chat"
        assert body["messages"] == MESSAGES
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Каберне подойдёт [1]"}}]
        })

    llm = DeepSeekLLM(
        base_url="https://api.deepseek.com", api_key="sk-test", model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    assert llm.chat(MESSAGES) == "Каберне подойдёт [1]"


def test_deepseek_missing_api_key_raises_llm_unavailable():
    llm = DeepSeekLLM(base_url="https://api.deepseek.com", api_key=None, model="deepseek-chat")
    with pytest.raises(LLMUnavailable):
        llm.chat(MESSAGES)


def test_deepseek_5xx_retries_then_raises_llm_unavailable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="upstream error")

    llm = DeepSeekLLM(
        base_url="https://api.deepseek.com", api_key="sk-test", model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMUnavailable):
        llm.chat(MESSAGES)
    assert calls["n"] == 3  # 1 попытка + 2 ретрая, как того требует контракт


def test_deepseek_4xx_does_not_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    llm = DeepSeekLLM(
        base_url="https://api.deepseek.com", api_key="sk-bad", model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMUnavailable):
        llm.chat(MESSAGES)
    assert calls["n"] == 1


def test_deepseek_chat_stream_yields_deltas():
    def handler(request: httpx.Request) -> httpx.Response:
        content = _sse(
            {"choices": [{"delta": {"content": "Каберне "}}]},
            {"choices": [{"delta": {"content": "подойдёт [1]"}}]},
        )
        return httpx.Response(200, content=content)

    llm = DeepSeekLLM(
        base_url="https://api.deepseek.com", api_key="sk-test", model="deepseek-chat",
        transport=httpx.MockTransport(handler),
    )
    chunks = list(llm.chat_stream(MESSAGES))
    assert "".join(chunks) == "Каберне подойдёт [1]"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def test_anthropic_chat_splits_system_and_parses_content_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "ak-test"
        body = json.loads(request.content)
        assert body["system"] == "Ты сомелье."
        assert body["messages"] == [{"role": "user", "content": "Что подать к стейку?"}]
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "Каберне [1]"}]
        })

    llm = AnthropicLLM(
        base_url="https://api.anthropic.com", api_key="ak-test",
        model="claude-sonnet-5", transport=httpx.MockTransport(handler),
    )
    assert llm.chat(MESSAGES) == "Каберне [1]"


def test_anthropic_chat_stream_reads_content_block_deltas():
    def handler(request: httpx.Request) -> httpx.Response:
        content = _sse(
            {"type": "content_block_delta", "delta": {"text": "Каберне "}},
            {"type": "content_block_delta", "delta": {"text": "подойдёт [1]"}},
            {"type": "message_stop"},
            done=False,
        )
        return httpx.Response(200, content=content)

    llm = AnthropicLLM(
        base_url="https://api.anthropic.com", api_key="ak-test",
        model="claude-sonnet-5", transport=httpx.MockTransport(handler),
    )
    assert "".join(llm.chat_stream(MESSAGES)) == "Каберне подойдёт [1]"


# ---------------------------------------------------------------------------
# GigaChat
# ---------------------------------------------------------------------------

def test_gigachat_fetches_token_then_calls_chat_and_caches_token():
    calls = {"oauth": 0, "chat": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth"):
            calls["oauth"] += 1
            return httpx.Response(200, json={"access_token": "tok-1", "expires_at": None})
        calls["chat"] += 1
        assert request.headers["authorization"] == "Bearer tok-1"
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ответ [1]"}}]})

    llm = GigaChatLLM(
        auth_url="https://ngw.example/api/v2/oauth", auth_key="base64creds",
        scope="GIGACHAT_API_PERS", base_url="https://gigachat.example/api/v1",
        model="GigaChat", transport=httpx.MockTransport(handler),
    )
    assert llm.chat(MESSAGES) == "Ответ [1]"
    assert llm.chat(MESSAGES) == "Ответ [1]"
    assert calls["oauth"] == 1, "токен должен кэшироваться между вызовами"
    assert calls["chat"] == 2


def test_gigachat_missing_auth_key_raises_llm_unavailable():
    llm = GigaChatLLM(
        auth_url="https://ngw.example/api/v2/oauth", auth_key=None,
        scope="GIGACHAT_API_PERS", base_url="https://gigachat.example/api/v1",
        model="GigaChat",
    )
    with pytest.raises(LLMUnavailable):
        llm.chat(MESSAGES)


def _spy_httpx_client(monkeypatch) -> dict:
    captured: dict = {}
    real_client = httpx.Client

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real_client(*args, **{**kwargs, "verify": True})

    monkeypatch.setattr("llm.drivers.gigachat.httpx.Client", spy)
    monkeypatch.setenv("GIGACHAT_AUTH_KEY", "test-key")
    return captured


def test_gigachat_ca_bundle_scopes_trust_to_gigachat_client(monkeypatch):
    """Корень Минцифры доверяется только клиенту GigaChat — через путь к PEM, а не отключением проверки."""
    from llm.drivers.gigachat import GigaChatLLM

    captured = _spy_httpx_client(monkeypatch)
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE", "/opt/somelye/certs/russian_trusted_root_ca.pem")
    monkeypatch.delenv("GIGACHAT_VERIFY_SSL", raising=False)
    GigaChatLLM.from_env()
    assert captured["verify"] == "/opt/somelye/certs/russian_trusted_root_ca.pem"


def test_gigachat_verify_ssl_false_wins_over_bundle(monkeypatch):
    from llm.drivers.gigachat import GigaChatLLM

    captured = _spy_httpx_client(monkeypatch)
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE", "/opt/somelye/certs/russian_trusted_root_ca.pem")
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL", "false")
    GigaChatLLM.from_env()
    assert captured["verify"] is False


def test_gigachat_defaults_to_system_trust_without_bundle(monkeypatch):
    from llm.drivers.gigachat import GigaChatLLM

    captured = _spy_httpx_client(monkeypatch)
    monkeypatch.delenv("GIGACHAT_CA_BUNDLE", raising=False)
    monkeypatch.delenv("GIGACHAT_VERIFY_SSL", raising=False)
    GigaChatLLM.from_env()
    assert captured["verify"] is True
