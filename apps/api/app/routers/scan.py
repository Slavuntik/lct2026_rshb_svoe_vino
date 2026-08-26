"""POST /scan/resolve (мок resolve_label) и POST /scan/ocr.

/scan/ocr: v0.2 сокращение до 10.09 (заметка оркестратора) — веб-скан фото
отложен, эндпоинт всегда отвечает 501 not_implemented. Форма запроса
(multipart image + explicit_consent) объявлена по контракту, чтобы
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
from ..security import Principal, fk_user_id, get_current_principal

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
        ScanMatch(
            wine_id=c.id, name=c.meta.get("name", c.id),
            winery_name=c.meta.get("winery_name", ""), confidence=round(c.score, 4),
        )
        for c in top
    ]
    low_confidence = (not matches) or matches[0].confidence < settings.low_confidence_threshold

    db.add(Scan(
        user_id=fk_user_id(principal), query_text=body.text,
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
