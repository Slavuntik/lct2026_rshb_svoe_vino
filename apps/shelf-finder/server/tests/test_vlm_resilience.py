"""Bounded local tests of the optional provider; no external gateway or weights."""

import asyncio
import json
import time

import httpx
from PIL import Image
import pytest

pytest.importorskip("torch")
from shelf_api.vlm import LiteLLMClient

WINE = {"wine": {"name": "Wine"}}
ITEMS = [{"box": [0, 0, 1, 1], "candidates": ["wine"]}]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"choices": None},
        {"choices": []},
        {"choices": [None]},
        {"choices": ["bad"]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": []}}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    ],
)
def test_bad_envelopes_have_controlled_validation_error(monkeypatch, payload):
    async def reply(*args):
        return payload

    monkeypatch.setattr(LiteLLMClient, "request", reply)
    with pytest.raises(ValueError):
        LiteLLMClient("https://provider.invalid/v1", "fake", "model").choose(
            Image.new("RGB", (40, 80)), ITEMS, WINE
        )


def test_cooldown_skips_network_and_success_resets_failures(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls = []

    def operation(*args):
        calls.append(1)
        if len(calls) <= 2:
            raise RuntimeError("simulated outage")
        return {0: "wine"}

    client = LiteLLMClient("https://provider.invalid/v1", "fake", "model")
    monkeypatch.setattr(client, "_choose", operation)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            client.choose(None, None, None)
    assert len(calls) == 2
    clock[0] += 31
    assert client.choose(None, None, None) == {0: "wine"}
    assert client.failures == 0 and client.retry_at == 0


@pytest.mark.parametrize("size,oversized", [(40, False), (262145, True)])
def test_streamed_provider_response_size_limit(monkeypatch, size, oversized):
    real_client = httpx.AsyncClient

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps({"text": "x" * size}).encode()

    def respond(request):
        assert request.headers["authorization"] == "Bearer fake"
        return httpx.Response(200, stream=Stream())

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw),
    )
    client = LiteLLMClient("https://provider.invalid/v1", "fake", "model")
    if oversized:
        with pytest.raises(ValueError, match="256 KiB"):
            asyncio.run(client.request({}, 1))
    else:
        assert asyncio.run(client.request({}, 1)) == {"text": "x" * size}


def test_valid_provider_reply_uses_allowed_catalog_slot(monkeypatch):
    async def reply(*args):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "bottles": [
                                    {
                                        "crop_id": 1,
                                        "selected_slot": "A",
                                        "evidence": "visible brand",
                                    }
                                ]
                            }
                        )
                    },
                }
            ]
        }

    monkeypatch.setattr(LiteLLMClient, "request", reply)
    client = LiteLLMClient("https://provider.invalid/v1", "fake", "model")
    assert client.choose(Image.new("RGB", (40, 80)), ITEMS, WINE) == {0: "wine"}
