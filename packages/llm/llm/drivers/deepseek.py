"""DeepSeek — OpenAI-совместимый HTTP-драйвер (прод демо).

env: LLM_BASE_URL (default https://api.deepseek.com), LLM_API_KEY (обязателен
при реальном вызове), LLM_MODEL (default deepseek-chat).
"""
from __future__ import annotations

import json
import os
from typing import Iterator

import httpx

from ..base import LLMUnavailable, Msg
from ._http import DEFAULT_TIMEOUT, open_stream_with_retries, request_json, wrap_stream_errors

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekLLM:
    provider = "deepseek"

    def __init__(self, *, base_url: str, api_key: str | None, model: str,
                 timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_env(cls) -> "DeepSeekLLM":
        return cls(
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ.get("LLM_API_KEY"),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise LLMUnavailable("LLM_API_KEY не задан для провайдера deepseek")
        return {
            "Authorization": f"Bearer {self.api_key}",
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
        body: dict = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        resp = request_json(
            self._client, "POST", f"{self.base_url}/chat/completions",
            headers=self._headers(), json=body,
        )
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable(f"неожиданный формат ответа deepseek: {exc}") from exc

    def chat_stream(
        self,
        messages: list[Msg],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        body = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        cm, resp = open_stream_with_retries(
            self._client, "POST", f"{self.base_url}/chat/completions",
            headers=self._headers(), json=body,
        )

        def _gen() -> Iterator[str]:
            try:
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, ValueError):
                        continue
                    if delta:
                        yield delta
            finally:
                cm.__exit__(None, None, None)

        yield from wrap_stream_errors(_gen)
