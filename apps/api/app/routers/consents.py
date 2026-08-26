"""GET/POST /consents. Доступно и гостю (его consent_ledger живёт под
анонимным id), и зарегистрированному пользователю — см. app/security.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ConsentLedger, User
from ..schemas import ConsentPostRequest, ConsentStateItem
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/consents", tags=["consents"])


@router.get("", response_model=list[ConsentStateItem])
def get_consents(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ConsentStateItem]:
    rows = (
        db.query(ConsentLedger)
        .filter(ConsentLedger.user_id == principal.id)
        .order_by(ConsentLedger.at.desc(), ConsentLedger.id.desc())
        .all()
    )
    latest_by_scope: dict[str, ConsentLedger] = {}
    for row in rows:
        latest_by_scope.setdefault(row.scope, row)
    return [
        ConsentStateItem(scope=r.scope, consent_version=r.consent_version, granted=r.granted, at=r.at)
        for r in latest_by_scope.values()
    ]


@router.post("", status_code=204)
def post_consents(
    body: ConsentPostRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    for scope in body.scopes:
        db.add(ConsentLedger(
            user_id=principal.id, consent_version=body.consent_version,
            scope=scope, granted=body.grant,
        ))

    # "Отзыв base = запрос на удаление аккаунта" (contracts/openapi.yaml).
    if not body.grant and "base" in body.scopes and principal.kind == "user":
        user = db.get(User, principal.id)
        if user is not None and user.deleted_at is None:
            user.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    return None
