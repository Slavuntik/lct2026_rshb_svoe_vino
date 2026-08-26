"""Anthropic Messages API (dev / judge в eval).

env: LLM_BASE_URL (default https://api.anthropic.com), LLM_API_KEY, LLM_MODEL
(default claude-3-5-haiku-latest — дешёвый дефолт для dev/eval-judge).

Messages API отделяет системный промпт от messages: все role="system" из
входного списка склеиваются в верхнеуровневое поле "system", остаются только
user/assistant.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

import httpx

from ..base import LLMUnavailable, Msg
from ._http import DEFAULT_TIMEOUT, open_stream_with_retries, request_json, wrap_stream_errors

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-3-5-haiku-latest"
ANTHROPIC_VERSION = "2023-06-01"


def _split_system(messages: list[Msg]) -> tuple[str | None, list[dict]]:
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    system = "\n\n".join(system_parts) if system_parts else None
    return system, rest


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, *, base_url: str, api_key: str | None, model: str,
                 timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_env(cls) -> "AnthropicLLM":
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("LLM_API_KEY"),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise LLMUnavailable("LLM_API_KEY не задан для провайдера anthropic")
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: list[Msg],
        *,
        json_mode: bool = False,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> str:
        system, rest = _split_system(messages)
        body: dict = {
            "model": self.model,
            "messages": rest,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        if json_mode:
            body["system"] = (system + "\n\n" if system else "") + (
                "Ответь строго валидным JSON-объектом, без пояснений и markdown."
            )
        resp = request_json(
            self._client, "POST", f"{self.base_url}/v1/messages",
            headers=self._headers(), json=body,
        )
        try:
            data = resp.json()
            return "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        except (KeyError, ValueError) as exc:
            raise LLMUnavailable(f"неожиданный формат ответа anthropic: {exc}") from exc

    def chat_stream(
        self,
        messages: list[Msg],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        system, rest = _split_system(messages)
        body: dict = {
            "model": self.model,
            "messages": rest,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        cm, resp = open_stream_with_retries(
            self._client, "POST", f"{self.base_url}/v1/messages",
            headers=self._headers(), json=body,
        )

        def _gen() -> Iterator[str]:
            try:
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text")
                        if text:
                            yield text
                    elif event.get("type") == "message_stop":
                        break
            finally:
                cm.__exit__(None, None, None)

        yield from wrap_stream_errors(_gen)
