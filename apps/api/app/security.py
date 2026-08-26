"""Пароли (argon2id), JWT, и зависимости аутентификации.

Модель принципала после v0.2 (появление /auth/guest):
  - "user"  — есть строка в users, id — её первичный ключ.
  - "guest" — анонимный uuid без строки в users; согласие base лежит в
    consent_ledger (там больше нет FK на users, так что это легально).

Гость допущен к scan/chat/wines/analogs (плюс /consents — это его же ledger),
но НЕ к /taste/* (нужен scope profiling, которого у гостя в принципе быть не
может — 403 consent_required) и не к /profile/data-export и DELETE /profile
(нечего экспортировать/удалять — нет учётной записи, 401 unauthorized).

Почему это вообще проверяется на бэкенде, а не только в клиенте: иначе 18+
гейт был бы фиктивным — его легко обойти прямым вызовом API мимо экрана
подтверждения возраста. Обязательный принципал (гость или юзер) на
scan/chat/wines/analogs — единственный способ реально закрыть эту дыру.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from .config import Settings, get_settings_dep
from .db import get_db
from .errors import ApiError
from .models import User

_hasher = PasswordHasher()


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class Principal:
    id: str
    kind: Literal["user", "guest"]


def make_access_token(principal_id: str, kind: str, settings: Settings) -> tuple[str, int]:
    now = int(time.time())
    expires_in = settings.jwt_expires_seconds
    payload = {"sub": principal_id, "kind": kind, "iat": now, "exp": now + expires_in}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ApiError(401, "unauthorized", "Токен недействителен или истёк") from exc


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def get_current_principal(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Principal:
    token = _extract_bearer(authorization)
    if not token:
        raise ApiError(401, "unauthorized", "Требуется Bearer-токен")
    payload = decode_access_token(token, settings)
    sub = payload.get("sub")
    kind = payload.get("kind", "user")
    if not sub:
        raise ApiError(401, "unauthorized", "Токен без субъекта")
    if kind == "user":
        user = db.get(User, sub)
        if user is None or user.deleted_at is not None:
            raise ApiError(401, "unauthorized", "Учётная запись недоступна")
    return Principal(id=sub, kind=kind)


def get_current_principal_optional(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Principal | None:
    token = _extract_bearer(authorization)
    if not token:
        return None
    try:
        payload = decode_access_token(token, settings)
    except ApiError:
        return None
    sub = payload.get("sub")
    kind = payload.get("kind", "user")
    if not sub:
        return None
    if kind == "user":
        user = db.get(User, sub)
        if user is None or user.deleted_at is not None:
            return None
    return Principal(id=sub, kind=kind)


def fk_user_id(principal: Principal) -> str | None:
    """Значение для колонок вида scans.user_id/chat_messages.user_id/events.user_id
    (FK на users, ON DELETE SET NULL/CASCADE) — гостя туда класть нельзя, для
    гостя в users никогда не было и не будет строки, а PRAGMA foreign_keys=ON
    в SQLite (см. app/db.py) реально проверяет существование строки при
    вставке. Consent_ledger — единственная таблица без такой FK (v0.2), туда
    guest-id пишется как есть (см. routers/auth.py, routers/consents.py).
    """
    return principal.id if principal.kind == "user" else None


def require_registered_user(
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """Для /profile/data-export и DELETE /profile: гостю тут делать нечего."""
    if principal.kind != "user":
        raise ApiError(401, "unauthorized", "Требуется полноценная регистрация")
    return principal


def require_profiling_consent(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Principal:
    """Для /taste/swipes и /taste/profile: нужен активный scope=profiling.

    Гость технически не может иметь этот scope (эта проверка отсекает его
    сразу, не дожидаясь похода в ledger) — так и задокументировано в
    contracts/openapi.yaml v0.2 (заметка ревью 01, блокер 3).
    """
    if principal.kind == "guest":
        raise ApiError(403, "consent_required", "Профилирование доступно только зарегистрированным")
    from .models import ConsentLedger  # локальный импорт: избежать цикла на уровне модуля

    latest = (
        db.query(ConsentLedger)
        .filter(ConsentLedger.user_id == principal.id, ConsentLedger.scope == "profiling")
        .order_by(ConsentLedger.at.desc(), ConsentLedger.id.desc())
        .first()
    )
    if latest is None or not latest.granted:
        raise ApiError(403, "consent_required", "Нужно согласие на профилирование (scope=profiling)")
    return principal
