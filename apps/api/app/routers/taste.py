"""POST /taste/swipes, GET /taste/profile — требуют scope profiling
(require_profiling_consent уже отсекает гостей и пользователей без согласия,
403 consent_required).

Истина — таблица swipes (комментарий в contracts/schema.sql); taste_profiles
— кэш, который эта пересчитывается на каждый /taste/swipes и никогда не
пишется из GET (чтобы у чтения не было побочных эффектов). Вектор и
top_styles — эвристика уровня MVP (никакой обученной модели нет ни в одном
контракте): усреднение сенсорного профиля лайкнутых вин, топ-3 самых частых
reference_style_matches среди них; без лайков — нейтральный вектор 0.5 и
пустой top_styles. Задокументировано как допущение в reports/b-report.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_retriever_dep
from ..models import Swipe, TasteProfile
from ..rag.interface import Retriever
from ..schemas import SwipeRequest, TasteProfileResponse
from ..security import Principal, require_profiling_consent

router = APIRouter(prefix="/taste", tags=["taste"])

_AXES = ("sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles")
_NEUTRAL_VECTOR = {a: 0.5 for a in _AXES}


def _compute_profile(db: Session, retriever: Retriever, user_id: str) -> tuple[dict[str, float], list[str], int]:
    swipes = db.query(Swipe).filter(Swipe.user_id == user_id).all()
    likes = [s for s in swipes if s.verdict == "like"]

    getter = getattr(retriever, "get_by_id", None)
    sums = {a: 0.0 for a in _AXES}
    style_counts: dict[str, int] = {}
    counted = 0
    for like in likes:
        wine = getter(like.wine_id) if getter else None
        if not wine:
            continue
        sensory = wine["derived"].get("sensory", {})
        for axis in _AXES:
            value = sensory.get(axis)
            if value is not None:
                sums[axis] += value
        counted += 1
        for style in wine["derived"].get("reference_style_matches", []):
            style_counts[style] = style_counts.get(style, 0) + 1

    if counted == 0:
        vector = dict(_NEUTRAL_VECTOR)
    else:
        vector = {axis: round(sums[axis] / counted, 4) for axis in _AXES}
    top_styles = [s for s, _ in sorted(style_counts.items(), key=lambda kv: -kv[1])][:3]
    return vector, top_styles, len(swipes)


@router.post("/swipes", status_code=204)
def post_swipe(
    body: SwipeRequest,
    principal: Principal = Depends(require_profiling_consent),
    retriever: Retriever = Depends(get_retriever_dep),
    db: Session = Depends(get_db),
) -> None:
    db.add(Swipe(user_id=principal.id, wine_id=body.wine_id, verdict=body.verdict))
    db.commit()

    vector, top_styles, swipes_count = _compute_profile(db, retriever, principal.id)
    profile = db.get(TasteProfile, principal.id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if profile is None:
        profile = TasteProfile(
            user_id=principal.id, vector=vector, top_styles=top_styles,
            swipes_count=swipes_count, updated_at=now,
        )
        db.add(profile)
    else:
        profile.vector = vector
        profile.top_styles = top_styles
        profile.swipes_count = swipes_count
        profile.updated_at = now
    db.commit()
    return None


@router.get("/profile", response_model=TasteProfileResponse)
def get_taste_profile(
    principal: Principal = Depends(require_profiling_consent),
    db: Session = Depends(get_db),
) -> TasteProfileResponse:
    profile = db.get(TasteProfile, principal.id)
    if profile is None:
        return TasteProfileResponse(vector=dict(_NEUTRAL_VECTOR), top_styles=[], swipes_count=0)
    return TasteProfileResponse(
        vector=profile.vector, top_styles=profile.top_styles, swipes_count=profile.swipes_count
    )
