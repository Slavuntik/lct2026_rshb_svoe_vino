"""GET /profile/data-export, DELETE /profile — оба требуют полноценной
регистрации (require_registered_user отсекает гостей: у гостя нет строки в
users, экспортировать/удалять как таковой профиль ему нечего).

DELETE /profile — soft-delete (users.deleted_at), НЕ немедленное удаление
строк из остальных таблиц: schema.sql комментирует users.deleted_at как
"soft-delete до фоновой очистки, затем строка удаляется" — сама фоновая
очистка вне зоны agents/B-api.md на этот MVP. consent_ledger НАМЕРЕННО
переживает удаление (v0.2, FK снят) — тест ниже это проверяет напрямую.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ChatMessage, ConsentLedger, Event, Feedback, Scan, Swipe, TasteProfile, User
from ..security import Principal, require_registered_user

router = APIRouter(prefix="/profile", tags=["profile"])


def _row_to_dict(row, exclude: set[str] = frozenset()) -> dict:
    return {
        col.name: getattr(row, col.name)
        for col in row.__table__.columns
        if col.name not in exclude
    }


@router.get("/data-export")
def data_export(
    principal: Principal = Depends(require_registered_user),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, principal.id)
    uid = principal.id
    taste_profile = db.get(TasteProfile, uid)
    return {
        # password_hash сознательно исключён из выгрузки: это не персональные
        # данные пользователя в смысле права на выгрузку, а секрет аутентификации.
        "user": _row_to_dict(user, exclude={"password_hash"}) if user else None,
        "consents": [
            _row_to_dict(r) for r in
            db.query(ConsentLedger).filter(ConsentLedger.user_id == uid).order_by(ConsentLedger.at).all()
        ],
        "swipes": [
            _row_to_dict(r) for r in
            db.query(Swipe).filter(Swipe.user_id == uid).order_by(Swipe.at).all()
        ],
        "taste_profile": _row_to_dict(taste_profile) if taste_profile else None,
        "scans": [
            _row_to_dict(r) for r in
            db.query(Scan).filter(Scan.user_id == uid).order_by(Scan.at).all()
        ],
        "chat_messages": [
            _row_to_dict(r) for r in
            db.query(ChatMessage).filter(ChatMessage.user_id == uid).order_by(ChatMessage.at).all()
        ],
        "events": [
            _row_to_dict(r) for r in
            db.query(Event).filter(Event.user_id == uid).order_by(Event.at).all()
        ],
        "feedback": [
            _row_to_dict(r) for r in
            db.query(Feedback).filter(Feedback.user_id == uid).order_by(Feedback.at).all()
        ],
    }


@router.delete("", status_code=204)
def delete_profile(
    principal: Principal = Depends(require_registered_user),
    db: Session = Depends(get_db),
) -> None:
    user = db.get(User, principal.id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if user is not None and user.deleted_at is None:
        user.deleted_at = now

    latest_scopes = (
        db.query(ConsentLedger.scope)
        .filter(ConsentLedger.user_id == principal.id)
        .distinct()
        .all()
    )
    scopes = {row[0] for row in latest_scopes} or {"base"}
    latest_version_row = (
        db.query(ConsentLedger)
        .filter(ConsentLedger.user_id == principal.id)
        .order_by(ConsentLedger.at.desc(), ConsentLedger.id.desc())
        .first()
    )
    consent_version = latest_version_row.consent_version if latest_version_row else "unknown"

    for scope in scopes:
        db.add(ConsentLedger(
            user_id=principal.id, consent_version=consent_version, scope=scope, granted=False,
        ))
    db.commit()
    return None
