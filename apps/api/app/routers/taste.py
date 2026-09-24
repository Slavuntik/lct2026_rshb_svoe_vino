"""POST /taste/swipes, GET /taste/profile, GET /taste/candidates — все три
требуют scope profiling (require_profiling_consent уже отсекает гостей и
пользователей без согласия, 403 consent_required).

/taste/candidates (v0.2.2, сигнатура retriever.candidates_for_taste
нормативна с v0.3): колода для свайп-дегустации — пробел нашёл агент C
(экран паспорта вкуса сидел на захардкоженном списке вин). Теперь часть
contracts/rag-interface.md — вызывается напрямую, без getattr-деградации.

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

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_retriever_dep
from ..models import Swipe, TasteProfile
from ..rag.interface import Retriever
from ..schemas import (
    AnalogsStyle,
    SwipeRequest,
    TasteCandidateItem,
    TasteCandidatesResponse,
    TasteProfileResponse,
)
from ..security import Principal, require_profiling_consent

router = APIRouter(prefix="/taste", tags=["taste"])

_AXES = ("sweetness", "acidity", "tannin", "body", "oak", "aromatic_intensity", "bubbles")
_NEUTRAL_VECTOR = {a: 0.5 for a in _AXES}

# contracts/openapi.yaml v0.3.6 (аудит architect, тот же класс дефекта, что
# WineResponse.similar — reports/qa-manual-hack-v16.md): top_styles — голые
# слаги pipeline/ref/reference_styles.yaml, TastePassportScreen.tsx рендерит
# их буквально. list_reference_styles(top_n=...) в РЕАЛЬНОЙ реализации
# (packages/rag/rag/styles.py::StyleMatcher.list_popular) — top-N ПО ЧАСТОТЕ
# в каталоге, не весь справочник по алфавиту/структуре, поэтому top_n нужен
# с запасом больше числа стилей вообще (146 в pipeline/ref/reference_styles.yaml
# на 22.09), не len(top_styles) — иначе редкий (но настоящий) стиль
# пользователя мог бы не попасть в top-N и потерять имя без всякой причины.
_STYLE_CATALOG_TOP_N = 500


def _compute_profile(db: Session, retriever: Retriever, user_id: str) -> tuple[dict[str, float], list[str], int]:
    swipes = db.query(Swipe).filter(Swipe.user_id == user_id).all()
    likes = [s for s in swipes if s.verdict == "like"]

    sums = {a: 0.0 for a in _AXES}
    style_counts: dict[str, int] = {}
    counted = 0
    for like in likes:
        # get_by_id — часть contracts/rag-interface.md с v0.2.1, отдаёт
        # Candidate (meta={"source":..., "derived":...} для вина), не dict.
        candidate = retriever.get_by_id(like.wine_id)
        if candidate is None or candidate.kind != "wine":
            continue
        derived = candidate.meta["derived"]
        sensory = derived.get("sensory", {})
        for axis in _AXES:
            value = sensory.get(axis)
            if value is not None:
                sums[axis] += value
        counted += 1
        for style in derived.get("reference_style_matches", []):
            style_counts[style] = style_counts.get(style, 0) + 1

    if counted == 0:
        vector = dict(_NEUTRAL_VECTOR)
    else:
        vector = {axis: round(sums[axis] / counted, 4) for axis in _AXES}
    top_styles = [s for s, _ in sorted(style_counts.items(), key=lambda kv: -kv[1])][:3]
    return vector, top_styles, len(swipes)


def _resolve_style_names(retriever: Retriever, style_slugs: list[str]) -> list[AnalogsStyle]:
    """top_styles (слаги) -> top_styles_named ({slug,name,country}) — тот же
    источник, что резолвит стиль в карточке/аналогах: retriever.
    list_reference_styles(), в конечном счёте pipeline/ref/
    reference_styles.yaml (packages/rag/rag/refdata.py, через StyleMatcher.
    by_slug). Один вызов на весь список (обычно <=3 слага, _compute_profile
    берёт топ-3) — не по одному резолву на слаг. Слаг вне справочника
    пропускается: остаётся в top_styles, не попадает в top_styles_named;
    порядок сохраняется 1:1 с порядком style_slugs."""
    if not style_slugs:
        return []
    catalog = {s["slug"]: s for s in retriever.list_reference_styles(top_n=_STYLE_CATALOG_TOP_N)}
    result: list[AnalogsStyle] = []
    for slug in style_slugs:
        style = catalog.get(slug)
        if style is None:
            continue
        result.append(AnalogsStyle(slug=style["slug"], name=style["name"], country=style["country"]))
    return result


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
    retriever: Retriever = Depends(get_retriever_dep),
    db: Session = Depends(get_db),
) -> TasteProfileResponse:
    profile = db.get(TasteProfile, principal.id)
    if profile is None:
        return TasteProfileResponse(
            vector=dict(_NEUTRAL_VECTOR), top_styles=[], top_styles_named=[], swipes_count=0
        )
    return TasteProfileResponse(
        vector=profile.vector, top_styles=profile.top_styles,
        top_styles_named=_resolve_style_names(retriever, profile.top_styles),
        swipes_count=profile.swipes_count,
    )


@router.get("/candidates", response_model=TasteCandidatesResponse)
def get_taste_candidates(
    limit: int = Query(default=20, ge=1, le=50),
    principal: Principal = Depends(require_profiling_consent),
    retriever: Retriever = Depends(get_retriever_dep),
    db: Session = Depends(get_db),
) -> TasteCandidatesResponse:
    already_swiped = [
        row[0] for row in db.query(Swipe.wine_id).filter(Swipe.user_id == principal.id).distinct()
    ]

    # Позиционный exclude_ids: list[str] — нормативно с v0.3, без keyword/getattr.
    candidates = retriever.candidates_for_taste(already_swiped, limit)

    wines = [
        # meta для kind=wine — {"source": {...}, "derived": {...}} (v0.3).
        TasteCandidateItem(
            wine_id=c.id, name=c.meta["source"].get("name", c.id),
            winery_name=c.meta["source"].get("winery_name", ""),
            region_name=c.meta["source"].get("region_name"), color=c.meta["source"].get("color"),
            image_url=c.meta["source"].get("image_url"),
        )
        for c in candidates
    ]
    return TasteCandidatesResponse(wines=wines)
