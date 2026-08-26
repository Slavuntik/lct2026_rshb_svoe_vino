"""Общие HTTP-хелперы для реальных драйверов (deepseek/gigachat/anthropic).

Правило контракта (contracts/llm-adapter.md): таймаут 30с, 2 ретрая с
бэкоффом; любая ошибка провайдера превращается в LLMUnavailable — вызывающая
сторона (API) обязана ответить честным refusal, а не 500.

Эти драйверы НЕ вызываются в тестах реальной сетью (запрещено правилами
проекта) — тесты проверяют только сборку запроса/разбор ответа через
httpx.MockTransport.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import httpx

from ..base import LLMUnavailable

DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 0.5

# Коды, при которых имеет смысл повторить попытку (перегрузка/сеть),
# в отличие от 4xx-ошибок конфигурации (неверный ключ и т.п.), которые
# ретраить бессмысленно.
_RETRYABLE_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    **kwargs: Any,
) -> httpx.Response:
    """POST/GET с ретраями. Поднимает LLMUnavailable вместо сетевых исключений."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            last_exc = exc
        except httpx.TransportError as exc:
            last_exc = exc
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code not in _RETRYABLE_STATUSES or attempt == retries:
                raise LLMUnavailable(
                    f"провайдер вернул {resp.status_code}: {resp.text[:200]}"
                )
            last_exc = None
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise LLMUnavailable(f"провайдер недоступен после {retries + 1} попыток: {last_exc}")


def open_stream_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_BACKOFF,
    **kwargs: Any,
):
    """Открывает httpx-стрим с ретраями на этапе установления соединения.

    Возвращает уже вошедший в контекст httpx.Response с открытым стримом.
    Ошибки посреди чтения стрима ретраями не покрываются — см. модульный
    докстринг: это осознанный компромисс для MVP.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        cm = client.stream(method, url, **kwargs)
        try:
            resp = cm.__enter__()
        except httpx.TimeoutException as exc:
            last_exc = exc
        except httpx.TransportError as exc:
            last_exc = exc
        else:
            if resp.status_code < 400:
                return cm, resp
            if resp.status_code not in _RETRYABLE_STATUSES or attempt == retries:
                cm.__exit__(None, None, None)
                raise LLMUnavailable(f"провайдер вернул {resp.status_code} на стриме")
            cm.__exit__(None, None, None)
            last_exc = None
        if attempt < retries:
            time.sleep(backoff * (attempt + 1))
    raise LLMUnavailable(f"провайдер недоступен после {retries + 1} попыток: {last_exc}")


def wrap_stream_errors(gen: Callable[[], Iterator[str]]) -> Iterator[str]:
    try:
        yield from gen()
    except httpx.TimeoutException as exc:
        raise LLMUnavailable(f"таймаут стрима: {exc}") from exc
    except httpx.TransportError as exc:
        raise LLMUnavailable(f"сеть недоступна на стриме: {exc}") from exc
