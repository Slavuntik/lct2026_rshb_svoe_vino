"""POST /events — единственная точка записи продуктовой аналитики. Доступна
без токена (до-аутентификационные события вроде age_gate_failed, тогда
user_id=NULL) и с токеном гостя/пользователя (тогда user_id — id принципала;
с v0.2.1 у гостя тоже полноценная строка users, поэтому FK events.user_id
не мешает и ему).

Сервер НЕ пишет события сам за другие эндпоинты (осознанное решение, см.
reports/b-report.md, «Предложения к контрактам» — единый источник записи
на стороне клиента проще тестировать и не даёт задвоений при ретраях).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import ApiError
from ..events_dict import EVENT_NAMES, FORBIDDEN_PROP_KEYS, MAX_PROP_STRING_LENGTH
from ..models import Event
from ..schemas import EventRequest
from ..security import Principal, get_current_principal_optional

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", status_code=204)
def post_event(
    body: EventRequest,
    principal: Principal | None = Depends(get_current_principal_optional),
    db: Session = Depends(get_db),
) -> None:
    if body.name not in EVENT_NAMES:
        raise ApiError(400, "unknown_event", f"Неизвестное имя события: {body.name!r}")

    bad_keys = FORBIDDEN_PROP_KEYS.intersection(body.props.keys())
    if bad_keys:
        raise ApiError(400, "validation_error", f"Недопустимые ключи в props: {sorted(bad_keys)}")
    for key, value in body.props.items():
        if isinstance(value, str) and len(value) > MAX_PROP_STRING_LENGTH:
            raise ApiError(400, "validation_error", f"Значение props.{key} похоже на свободный текст")

    props = dict(body.props)
    props.setdefault("_v", "0.1")

    db.add(Event(user_id=principal.id if principal else None, name=body.name, props=props))
    db.commit()
    return None
