"""POST /waitlist — лендинг, без auth. Повторная отправка той же почты
идемпотентна (204): в словаре ошибок нет кода "уже в листе ожидания", и
переспрашивать пользователя, почему форма "упала" на повторном клике — плохой
UX для лендинга.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Waitlist
from ..ratelimit import rate_limit
from ..schemas import WaitlistRequest

router = APIRouter(prefix="/waitlist", tags=["waitlist"])


@router.post("", status_code=204)
def join_waitlist(
    body: WaitlistRequest,
    db: Session = Depends(get_db),
    _rl: None = Depends(rate_limit("waitlist")),
) -> None:
    existing = db.query(Waitlist).filter(Waitlist.email == body.email).first()
    if existing is None:
        db.add(Waitlist(email=body.email, consent_version=body.consent_version))
    else:
        existing.consent_version = body.consent_version
    db.commit()
    return None
