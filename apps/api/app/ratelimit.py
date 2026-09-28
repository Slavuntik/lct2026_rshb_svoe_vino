"""Простая своя корзина для rate limit на auth и waitlist (без slowapi/Redis —
однопроцессный dev/demo, per B-api.md шаг 6). Скользящее окно по IP клиента.
"""
from __future__ import annotations

import time
import math
from collections import defaultdict, deque

from fastapi import Depends, Request, HTTPException

from .config import Settings, get_settings_dep
from .util import client_ip

# bucket_name -> ip -> deque[timestamps]
_HITS: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def reset_rate_limits() -> None:
    """Только для тестов — иначе тесты делят состояние через module-level dict."""
    _HITS.clear()


def rate_limit(bucket_name: str, *, max_requests: int | None = None):
    def dependency(request: Request, settings: Settings = Depends(get_settings_dep)) -> None:
        ip = client_ip(request)
        now = time.monotonic()
        window = settings.rate_limit_window_seconds
        limit = settings.rate_limit_max_requests if max_requests is None else max_requests
        hits = _HITS[bucket_name][ip]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(window - (now - hits[0])))
            raise HTTPException(429, "Слишком много попыток, попробуйте позже",
                                headers={"Retry-After": str(retry_after)})
        hits.append(now)

    return dependency
