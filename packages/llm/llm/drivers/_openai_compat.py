"""Общая реализация для драйверов, говорящих форматом OpenAI Chat Completions.

Используется двумя драйверами:
  - drivers/deepseek.py — облачный API DeepSeek (есть публичный дефолт base_url);
  - drivers/openai.py   — универсальный шлюз (LiteLLM/vLLM и т.п. перед своей
    моделью на GPU-сервере команды; base_url ОБЯЗАН быть задан явно, дефолта
    нет — адрес шлюза секретный).

Оба говорят одним и тем же протоколом (`POST {base_url}/chat/completions`,
`Authorization: Bearer <ключ>`, SSE `data: {...}\\n\\n` / `data: [DONE]`) —
сборка запроса и разбор ответа/стрима здесь одни, наследники задают только
`provider` и дефолты `from_env()` (contracts/llm-adapter.md: таймаут 30с,
2 ретрая с бэкоффом — см. _http.py; любая ошибка провайдера превращается в
LLMUnavailable, вызывающая сторона отвечает refusal, не 500).
"""
from __future__ import annotations

import json
from typing import Iterator

import httpx

from ..base import LLMUnavailable, Msg
from ._http import DEFAULT_TIMEOUT, open_stream_with_retries, request_json, wrap_stream_errors


class OpenAICompatLLM:
    """База: HTTP-механика Chat Completions. `provider` переопределяет наследник."""

    provider = "openai-compat"

    def __init__(self, *, base_url: str | None, api_key: str | None, model: str,
                 timeout: float = DEFAULT_TIMEOUT,
                 transport: httpx.BaseTransport | None = None) -> None:
        # base_url=None легален на конструкторе (см. drivers/openai.py — адрес
        # шлюза не хардкодится) — не трогаем сеть здесь, падаем лениво в
        # _require_base_url() при реальном вызове, тем же приёмом, что и
        # отсутствующий api_key в _headers().
        self.base_url = base_url.rstrip("/") if base_url else None
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def _require_base_url(self) -> str:
        if not self.base_url:
            raise LLMUnavailable(f"LLM_BASE_URL не задан для провайдера {self.provider}")
        return self.base_url

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise LLMUnavailable(f"LLM_API_KEY не задан для провайдера {self.provider}")
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
        base_url = self._require_base_url()
        headers = self._headers()
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
            self._client, "POST", f"{base_url}/chat/completions",
            headers=headers, json=body,
        )
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable(f"неожиданный формат ответа {self.provider}: {exc}") from exc

    def chat_stream(
        self,
        messages: list[Msg],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        base_url = self._require_base_url()
        headers = self._headers()
        body = {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        cm, resp = open_stream_with_retries(
            self._client, "POST", f"{base_url}/chat/completions",
            headers=headers, json=body,
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
