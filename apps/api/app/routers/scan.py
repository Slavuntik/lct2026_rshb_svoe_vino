"""POST /scan/resolve (мок resolve_label), POST /scan/ocr (501, не трогать)
и POST /scan/photo — кейс ЛЦТ (contracts/image-scan.md v0.4).

/scan/ocr: контракт v0.3 формализовал решение ревью 01 — веб-скан фото
отложен за MVP, эндпоинт ВСЕГДА отвечает 501 not_implemented (раньше
openapi.yaml ещё обещал 200/422, это было приведено к реальности). Форма
запроса (multipart image + explicit_consent) в контракте осталась — чтобы
сгенерированная OpenAPI-схема совпадала по путям/методам/форме — но тело не
разбирается, ответ 501 отдаётся немедленно. НЕ трогать (прямое указание
оркестратора этой волны) — /scan/photo ниже отдельный, самостоятельный путь.

/scan/photo: БЕЗ авторизации по умолчанию (опциональный принципал) — сюда
бьёт скрипт оценки кейса напрямую, без токена (case.md: "bash шлёт фото по
одному"); авторизованный вызов (гость/юзер приложения) просто атрибутирует
запись в scans, ничего не требует. Это сознательное отступление от общего
правила "scan/chat/wines/analogs требуют принципала" (app/security.py) —
здесь внешний контракт оценки жёстче внутренней политики 18+, а мобильный
UI (агент C) отвечает за то, чтобы не показывать сканер, минуя свой
собственный экран согласия.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from ..config import Settings, get_settings_dep
from ..cv.eval_report import read_eval_report
from ..cv.interface import ImageIndex, LabelVerifier
from ..cv.service import PhotoScanResult, run_photo_scan
from ..db import get_db
from ..deps import get_image_index_dep, get_label_verifier_dep, get_retriever_dep
from ..errors import ApiError
from ..models import Scan
from ..rag.interface import Retriever
from ..schemas import (
    AnalogsWineItem,
    ScanConfidence,
    ScanMatch,
    ScanPhotoFlatResponse,
    ScanPhotoRichResponse,
    ScanResolveRequest,
    ScanResolveResponse,
)
from ..security import Principal, get_current_principal, get_current_principal_optional

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


def _record_photo_scan(
    db: Session, principal: Principal | None, num_bytes: int, result: PhotoScanResult, *, best_effort: bool
) -> None:
    try:
        db.add(Scan(
            user_id=principal.id if principal else None,
            query_text=f"[photo upload, {num_bytes} bytes]",
            matched=result.best_guess_slug,
            confidence=result.top1_score,
        ))
        db.commit()
    except Exception:
        if not best_effort:
            raise
        # flat-режим обязан пережить что угодно, включая сбой записи в БД —
        # это диагностика, не часть контракта со скриптом оценки.
        db.rollback()


def _build_confidence(settings: Settings, result: PhotoScanResult) -> ScanConfidence:
    report = read_eval_report(settings.cv_eval_report_path)
    return ScanConfidence(
        top1_score=result.top1_score,
        gap=result.gap,
        f1_top1=(report or {}).get("f1_top1"),
        f1_top5=(report or {}).get("f1_top5"),
        eval_missing=report is None,
    )


@router.post("/photo")
def scan_photo(
    image: UploadFile = File(...),
    flat: bool = Query(default=False, description="?flat=1 — режим скрипта оценки"),
    principal: Principal | None = Depends(get_current_principal_optional),
    image_index: ImageIndex = Depends(get_image_index_dep),
    verifier: LabelVerifier = Depends(get_label_verifier_dep),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
):
    data = image.file.read()

    if flat:
        # contracts/image-scan.md: flat ВСЕГДА отдаёт валидный {"slug": "..."}
        # — скрипт оценки не должен споткнуться НИ О ЧТО (пустой/битый файл,
        # переполнение, сбой БД). Честность про уверенность — только в rich.
        if not data or len(data) > settings.max_upload_bytes:
            return ScanPhotoFlatResponse(slug="")
        try:
            result = run_photo_scan(
                image_bytes=data, image_index=image_index, verifier=verifier,
                retriever=retriever, settings=settings,
            )
        except ValueError:
            return ScanPhotoFlatResponse(slug="")
        _record_photo_scan(db, principal, len(data), result, best_effort=True)
        return ScanPhotoFlatResponse(slug=result.best_guess_slug or "")

    # rich-режим (UI) — честные ошибки, как везде в API.
    if not data:
        raise ApiError(400, "validation_error", "Пустой файл изображения")
    if len(data) > settings.max_upload_bytes:
        raise ApiError(400, "validation_error", f"Файл больше {settings.max_upload_bytes} байт")
    try:
        result = run_photo_scan(
            image_bytes=data, image_index=image_index, verifier=verifier,
            retriever=retriever, settings=settings,
        )
    except ValueError as exc:
        raise ApiError(400, "validation_error", f"Не удалось обработать изображение: {exc}") from exc

    _record_photo_scan(db, principal, len(data), result, best_effort=False)

    return ScanPhotoRichResponse(
        slug=result.slug,
        card=result.card,
        confidence=_build_confidence(settings, result),
        ocr_verified=result.ocr_verified,
        timing_ms=result.timing_ms,
        not_in_catalog=result.not_in_catalog,
        similar=[AnalogsWineItem(**item) for item in result.similar],
        analogs=[AnalogsWineItem(**item) for item in result.analogs],
    )
