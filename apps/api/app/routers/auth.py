from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..config import Settings, get_settings_dep
from ..db import get_db
from ..errors import ApiError
from ..models import ConsentLedger, User
from ..ratelimit import rate_limit
from ..schemas import GuestRequest, LoginRequest, RegisterRequest, TokenPair
from ..security import hash_password, make_access_token, try_resolve_guest_user, verify_password
from ..util import client_ip, hash_ip

router = APIRouter(prefix="/auth", tags=["auth"])


def _age_years(birth_date: date, today: date) -> int:
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/register", response_model=TokenPair, status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    _rl: None = Depends(rate_limit("auth")),
) -> TokenPair:
    """v0.2.1: с гостевым Bearer-токеном в заголовке — апгрейд ТОЙ ЖЕ строки
    users (email/password_hash/birth_date заполняются, is_guest=false),
    история scans/chat_messages/events/feedback на этот id сохраняется, т.к.
    id строки не меняется. Без токена (или с токеном, который не резолвится
    в живого гостя) — обычная новая строка."""
    if _age_years(body.birth_date, date.today()) < settings.min_age_years:
        raise ApiError(403, "age_restricted", f"Регистрация доступна только с {settings.min_age_years} лет")

    if "base" not in body.consent_scopes:
        raise ApiError(400, "validation_error", "Необходимо согласие на базовую обработку данных (scope=base)")

    existing = db.query(User).filter(User.email == body.email).first()
    if existing is not None:
        raise ApiError(400, "validation_error", "Пользователь с такой почтой уже зарегистрирован")

    guest_user = try_resolve_guest_user(request.headers.get("authorization"), db, settings)
    now = _utcnow()

    if guest_user is not None:
        user = guest_user
        user.email = body.email
        user.password_hash = hash_password(body.password)
        user.birth_date = body.birth_date
        user.is_guest = False
        user.age_confirmed_at = user.age_confirmed_at or now  # сохраняем момент первого 18+
    else:
        user = User(
            email=body.email,
            password_hash=hash_password(body.password),
            birth_date=body.birth_date,
            is_guest=False,
            age_confirmed_at=now,
        )
        db.add(user)
    db.flush()

    ip_hash = hash_ip(client_ip(request))
    for scope in body.consent_scopes:
        db.add(ConsentLedger(
            user_id=user.id, consent_version=body.consent_version,
            scope=scope, granted=True, ip_hash=ip_hash,
        ))
    db.commit()

    token, expires_in = make_access_token(user.id, settings)
    return TokenPair(access_token=token, expires_in=expires_in)


@router.post("/login", response_model=TokenPair)
def login(
    body: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    _rl: None = Depends(rate_limit("auth")),
) -> TokenPair:
    user = db.query(User).filter(User.email == body.email).first()
    if user is None or user.deleted_at is not None or not verify_password(body.password, user.password_hash):
        raise ApiError(401, "invalid_credentials", "Неверная почта или пароль")

    token, expires_in = make_access_token(user.id, settings)
    return TokenPair(access_token=token, expires_in=expires_in)


@router.post("/guest", response_model=TokenPair, status_code=201)
def guest(
    body: GuestRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    _rl: None = Depends(rate_limit("auth")),
) -> TokenPair:
    """v0.2.1: гость — полноценная строка users (is_guest=True, NULL-identity),
    не просто уникальный id, как было в v0.2 — так FK у scans/chat_messages/
    events/feedback работают и на неё, а /auth/register с этим токеном может
    её дозаполнить (см. register() выше)."""
    if not body.age_confirmed:
        raise ApiError(403, "age_restricted", f"Гостевой доступ только с {settings.min_age_years} лет")

    user = User(is_guest=True, age_confirmed_at=_utcnow())
    db.add(user)
    db.flush()

    ip_hash = hash_ip(client_ip(request))
    db.add(ConsentLedger(
        user_id=user.id, consent_version=body.consent_version,
        scope="base", granted=True, ip_hash=ip_hash,
    ))
    db.commit()

    token, expires_in = make_access_token(user.id, settings)
    return TokenPair(access_token=token, expires_in=expires_in)
