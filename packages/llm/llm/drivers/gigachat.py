"""GigaChat — OAuth client-credentials + REST (следующий прод).

env: GIGACHAT_AUTH_URL (default офиц. OAuth-эндпоинт Sber), GIGACHAT_AUTH_KEY
(base64 "client_id:client_secret", как выдаёт кабинет GigaChat API),
GIGACHAT_SCOPE (default GIGACHAT_API_PERS), LLM_BASE_URL (default
https://gigachat.devices.sberbank.ru/api/v1), LLM_MODEL (default GigaChat),
GIGACHAT_CA_BUNDLE (путь к PEM с корневым сертификатом Минцифры «Russian Trusted
Root CA»: цепочки ngw/gigachat.devices.sberbank.ru упираются в него, в стандартных
хранилищах его нет — без бандла TLS-проверка падает с «self-signed certificate in
chain»; бандл применяется ТОЛЬКО к клиенту GigaChat, остальной трафик ему не доверяет),
GIGACHAT_VERIFY_SSL (default "true"; "false" отключает проверку совсем — только для
отладки, бандл предпочтительнее).

Токен кэшируется в памяти процесса и обновляется по истечении.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Iterator

import httpx

from ..base import LLMUnavailable, Msg
from ._http import DEFAULT_TIMEOUT, open_stream_with_retries, request_json, wrap_stream_errors

DEFAULT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"
DEFAULT_MODEL = "GigaChat"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"

# Обновляем токен чуть раньше формального истечения, чтобы не словить 401
# на границе.
_TOKEN_SAFETY_MARGIN_S = 30


class GigaChatLLM:
    provider = "gigachat"

    def __init__(
        self,
        *,
        auth_url: str,
        auth_key: str | None,
        scope: str,
        base_url: str,
        model: str,
        verify_ssl: bool | str = True,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.auth_url = auth_url
        self.auth_key = auth_key
        self.scope = scope
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=timeout, verify=verify_ssl, transport=transport)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @classmethod
    def from_env(cls) -> "GigaChatLLM":
        verify: bool | str = os.environ.get("GIGACHAT_VERIFY_SSL", "true").strip().lower() not in (
            "0", "false", "no",
        )
        ca_bundle = os.environ.get("GIGACHAT_CA_BUNDLE")
        if verify and ca_bundle:
            verify = ca_bundle
        return cls(
            auth_url=os.environ.get("GIGACHAT_AUTH_URL", DEFAULT_AUTH_URL),
            auth_key=os.environ.get("GIGACHAT_AUTH_KEY"),
            scope=os.environ.get("GIGACHAT_SCOPE", DEFAULT_SCOPE),
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            verify_ssl=verify,
        )

    def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.auth_key:
            raise LLMUnavailable("GIGACHAT_AUTH_KEY не задан для провайдера gigachat")
        resp = request_json(
            self._client, "POST", self.auth_url,
            headers={
                "Authorization": f"Basic {self.auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": self.scope},
        )
        try:
            data = resp.json()
            token = data["access_token"]
            # GigaChat отдаёт expires_at в мс-эпохе; на всякий случай считаем
            # и от now(), если поле отсутствует.
            expires_at_ms = data.get("expires_at")
            if expires_at_ms:
                ttl = max(0.0, expires_at_ms / 1000 - time.time())
            else:
                ttl = 1800.0
        except (KeyError, ValueError) as exc:
            raise LLMUnavailable(f"неожиданный формат OAuth-ответа gigachat: {exc}") from exc
        self._token = token
        self._token_expires_at = time.monotonic() + max(0.0, ttl - _TOKEN_SAFETY_MARGIN_S)
        return token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
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
            # У GigaChat нет отдельного response_format — просим явно в system
            # было бы задачей промпт-сборщика; здесь только помечаем намерение.
            body["function_call"] = "none"
        resp = request_json(
            self._client, "POST", f"{self.base_url}/chat/completions",
            headers=self._headers(), json=body,
        )
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMUnavailable(f"неожиданный формат ответа gigachat: {exc}") from exc

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
