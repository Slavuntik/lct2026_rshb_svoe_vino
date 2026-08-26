"""Мелкие хелперы, общие для нескольких роутеров/зависимостей."""
from __future__ import annotations

import hashlib
from datetime import date

from fastapi import Request


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def hash_ip(ip: str, *, day: date | None = None) -> str:
    """sha256(ip + соль суток) — соль суток, НЕ сырой IP, в consent_ledger.ip_hash."""
    salt = (day or date.today()).isoformat()
    return hashlib.sha256(f"{ip}:{salt}".encode("utf-8")).hexdigest()
