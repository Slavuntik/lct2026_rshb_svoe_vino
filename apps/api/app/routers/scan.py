"""POST /scan/resolve (мок resolve_label) и POST /scan/ocr.

/scan/ocr: контракт v0.3 формализовал решение ревью 01 — веб-скан фото
отложен за MVP, эндпоинт ВСЕГДА отвечает 501 not_implemented (раньше
openapi.yaml ещё обещал 200/422, это было приведено к реальности). Форма
запроса (multipart image + explicit_consent) в контракте осталась — чтобы
сгенерированная OpenAPI-схема совпадала по путям/методам/форме — но тело не
разбирается, ответ 501 отдаётся немедленно.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from ..config import Settings, get_settings_dep
from ..db import get_db
from ..errors import ApiError
from ..models import Scan
from ..rag.interface import Retriever
from ..deps import get_retriever_dep
from ..schemas import ScanMatch, ScanResolveRequest, ScanResolveResponse
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("/resolve", response_model=ScanResolveResponse)
def scan_resolve(
    body: ScanResolveRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
) -> ScanResolveResponse:
    hints = body.hints.model_dump(exclude_none=True) if body.hints else None
    candidates = retriever.resolve_label(body.text, hints)
    top = candidates[:5]
    matches = [
        # meta для kind=wine — {"source": {...}, "derived": {...}} (v0.3).
        ScanMatch(
            wine_id=c.id, name=c.meta["source"].get("name", c.id),
            winery_name=c.meta["source"].get("winery_name", ""), confidence=round(c.score, 4),
        )
        for c in top
    ]
    low_confidence = (not matches) or matches[0].confidence < settings.low_confidence_threshold

    db.add(Scan(
        # v0.2.1: гость — полноценная строка users, FK работает и на него.
        user_id=principal.id, query_text=body.text,
        matched=matches[0].wine_id if matches else None,
        confidence=matches[0].confidence if matches else None,
    ))
    db.commit()

    return ScanResolveResponse(matches=matches, low_confidence=low_confidence)


@router.post("/ocr")
def scan_ocr(
    image: UploadFile = File(...),
    explicit_consent: bool = Form(...),
    principal: Principal = Depends(get_current_principal),
) -> None:
    raise ApiError(
        501, "not_implemented",
        "Веб-скан фото временно отключён — используйте /scan/resolve с текстом с этикетки",
    )
