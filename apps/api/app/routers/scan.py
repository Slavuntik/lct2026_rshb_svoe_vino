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

v0.4.4 (ревью 04, блокер 1) — "несгораемость flat ДО КОНЦА", три независимые
подстраховки под неизвестный формат скрипта кейсодержателя: (1) flat ловит
ЛЮБОЕ исключение (`except Exception`, не только `ValueError`) -> всегда
`{"slug": ""}`, никогда 4xx/5xx; (2) файловое поле принимается ПЕРВЫМ по
порядку формы, независимо от имени (`_first_uploaded_file` — быстрый путь на
буквальное имя "image", иначе честный обход `await request.form()`);
(3) `SCAN_FLAT_DEFAULT=1` включает flat-поведение без query-параметра вообще
(явный `?flat=0/1` всегда важнее этого дефолта). Лимит размера — 25 МБ
(`SCAN_MAX_UPLOAD_BYTES`, было 8 МБ — телефонные фото часто больше).

agents/B3-eval-route.md — `flat_scan_response()` ниже несёт ровно эту
несгораемую семантику как отдельно вызываемая функция, а не только ветку
`scan_photo`: `routers/eval.py::POST /v1/eval/predict` (эндпоинт скрипта
кейсодержателя, case-data/eval/participant_test.sh) — общий обработчик с
`/scan/photo?flat=1`, БЕЗ копипасты логики.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..config import Settings, get_settings_dep
from ..cv.eval_report import read_eval_report
from ..cv.interface import ImageIndex, LabelVerifier
from ..cv.archive import archive_scan_safe
from ..cv.service import PhotoScanResult, run_photo_scan
from ..cv.user_box import apply_user_box
from ..db import get_db
from ..deps import get_image_index_dep, get_label_verifier_dep, get_retriever_dep
from ..errors import ApiError
from ..foreign_scan_lookup import resolve_foreign_analogs
from ..models import Scan
from ..rag.interface import Retriever
from ..schemas import (
    AnalogsWineItem,
    PhotoMatchItem,
    ScanCandidateItem,
    ScanConfidence,
    ScanMatch,
    ScanPhotoFlatResponse,
    ScanPhotoRichResponse,
    ScanResolveRequest,
    ScanResolveResponse,
)
from ..security import Principal, get_current_principal, get_current_principal_optional

router = APIRouter(prefix="/scan", tags=["scan"])

logger = logging.getLogger(__name__)
# Задача тимлида 22.09 (reports/devops-stand-vlm.md: "источник текста этикетки vlm/ocr
# нигде не виден снаружи процесса"): logger.info() ниже несёт источник чтения этикетки.
# ДО 22.09 (reports/backend-text-source.md) здесь стоял точечный handler+setLevel —
# единственный способ долететь до вывода под голым `uvicorn app.main:app`
# (infra/ams3/somelye-api.service, infra/Dockerfile.api — без --log-level), т.к. root
# по Python-дефолту на WARNING без хендлеров. Решение тимлида 22.09 (п.4, тот же
# отчёт): убрать точечные хендлеры по файлам, настроить ОДИН handler+INFO на логгер
# пространства имён "app" целиком, в app/main.py — этот модуль (`app.routers.scan`,
# дочерний логгер) получает и уровень, и вывод через propagate (Python-дефолт, не
# трогаем), без своего handler'а. Два handler'а на одну запись дали бы дубли строк
# в выводе процесса — ровно то, чего просил избежать тимлид.


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

    # agents/B7-foreign-analogs.md: каталог vines не дал НИ ОДНОГО совпадения
    # (пустой matches, не просто низкая уверенность верхнего) — фолбэк на
    # аналог по стилю/сорту, распознанному в тексте по pipeline/ref. Непустые
    # matches — поведение не меняется вовсе (регрессия), даже если top
    # confidence ниже порога.
    analogs: list[AnalogsWineItem] = []
    analog_reason: str | None = None
    if not matches:
        analogs, analog_reason = resolve_foreign_analogs(retriever, body.text, settings)

    db.add(Scan(
        # v0.2.1: гость — полноценная строка users, FK работает и на него.
        user_id=principal.id, query_text=body.text,
        matched=matches[0].wine_id if matches else None,
        confidence=matches[0].confidence if matches else None,
    ))
    db.commit()

    return ScanResolveResponse(
        matches=matches, low_confidence=low_confidence,
        analogs=analogs, analog_reason=analog_reason,
    )


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


async def _first_uploaded_file(
    request: Request, named: UploadFile | None
) -> UploadFile | StarletteUploadFile | None:
    """v0.4.4 (ревью 04, блокер 1): "принимает первое файловое поле multipart
    независимо от имени" — скрипт кейсодержателя может назвать поле не
    `image`. Быстрый путь: если FastAPI уже связал параметр `image` (поле
    называется буквально "image", как во всех наших тестах) — используем его
    без повторного разбора формы. Иначе — единственный честный способ найти
    файл под ДРУГИМ именем: самим пройтись по `await request.form()` и взять
    первое значение файлового типа, в порядке полей формы.

    ВАЖНО: значения из `request.form()` — это `starlette.datastructures.
    UploadFile` (парсер Starlette), а НЕ `fastapi.UploadFile` — тот лишь
    подкласс, добавленный самим FastAPI при связывании параметров (см.
    `fastapi.UploadFile.__mro__`). `isinstance(value, fastapi.UploadFile)`
    здесь всегда даёт False — проверяем против базового класса Starlette,
    которому оба варианта реально принадлежат."""
    if named is not None:
        return named
    form = await request.form()
    for value in form.values():
        if isinstance(value, StarletteUploadFile):
            return value
    return None


async def flat_scan_response(
    request: Request,
    image: UploadFile | None,
    principal: Principal | None,
    image_index: ImageIndex,
    verifier: LabelVerifier,
    retriever: Retriever,
    settings: Settings,
    db: Session,
    raw_box: str | None = None,
) -> ScanPhotoFlatResponse:
    """Несгораемая flat-семантика (contracts/image-scan.md v0.4.4, "Режимы
    ответа API"): ЛЮБОЙ сбой -> валидный `{"slug": "<лучшая догадка>"}`,
    HTTP 200, никогда исключение/4xx/5xx наружу. Общий обработчик для ДВУХ
    путей: `/scan/photo?flat=1` (или `SCAN_FLAT_DEFAULT=1`) и
    `/v1/eval/predict` (`routers/eval.py`, алиас для скрипта кейсодержателя
    agents/B3-eval-route.md) — оба зовут ровно эту функцию, не копия логики.
    """
    upload = await _first_uploaded_file(request, image)
    data = await upload.read() if upload is not None else b""

    # contracts/image-scan.md (v0.4.4, ревью 04, блокер 1): flat ловит
    # ЛЮБОЕ исключение — не только ValueError (декодер/индекс/БД/что
    # угодно) — скрипт оценки не должен споткнуться НИ О ЧЕМ. Честность
    # про уверенность — только в rich.
    try:
        if upload is None or not data or len(data) > settings.max_upload_bytes:
            return ScanPhotoFlatResponse(slug="")
        # v0.4.10: рамка пользователя. Во flat негодная рамка НЕ ломает ответ — она просто
        # игнорируется: у скрипта оценки поля box нет вовсе, и появиться оно может только
        # по ошибке, а несгораемость важнее аккуратности ввода.
        try:
            data = apply_user_box(data, raw_box)
        except ValueError:
            pass
        result = run_photo_scan(
            image_bytes=data, image_index=image_index, verifier=verifier,
            retriever=retriever, settings=settings,
        )
        _record_photo_scan(db, principal, len(data), result, best_effort=True)
        return ScanPhotoFlatResponse(slug=result.best_guess_slug or "")
    except Exception:
        return ScanPhotoFlatResponse(slug="")


@router.post("/photo")
async def scan_photo(
    request: Request,
    background_tasks: BackgroundTasks,
    image: UploadFile | None = File(default=None),
    box: str | None = Form(
        default=None,
        description='рамка пользователя «x1,y1,x2,y2» в долях кадра (0…1); без неё бутылку выбирает движок',
    ),
    flat: bool | None = Query(
        default=None,
        description="?flat=1 — режим скрипта оценки; без параметра решает env SCAN_FLAT_DEFAULT",
    ),
    principal: Principal | None = Depends(get_current_principal_optional),
    image_index: ImageIndex = Depends(get_image_index_dep),
    verifier: LabelVerifier = Depends(get_label_verifier_dep),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
):
    # v0.4.4: явный ?flat=0/1 в запросе всегда важнее env-дефолта — тот лишь
    # страховка на случай, если скрипт кейсодержателя вообще не знает про
    # query-параметр.
    effective_flat = settings.scan_flat_default if flat is None else flat

    if effective_flat:
        return await flat_scan_response(
            request, image, principal, image_index, verifier, retriever, settings, db, box,
        )

    # rich-режим (UI) — честные ошибки, как везде в API.
    upload = await _first_uploaded_file(request, image)
    data = await upload.read() if upload is not None else b""
    if upload is None or not data:
        raise ApiError(400, "validation_error", "Пустой файл изображения")
    if len(data) > settings.max_upload_bytes:
        raise ApiError(400, "validation_error", f"Файл больше {settings.max_upload_bytes} байт")
    # v0.4.10: рамка пользователя применяется ДО движка — контракт ImageIndex не меняется,
    # и прицел работает у обоих провайдеров. В rich негодная рамка — честная 400, как и
    # любой другой негодный ввод.
    try:
        data = apply_user_box(data, box)
    except ValueError as exc:
        raise ApiError(400, "validation_error", f"Негодная рамка: {exc}") from exc
    try:
        result = run_photo_scan(
            image_bytes=data, image_index=image_index, verifier=verifier,
            retriever=retriever, settings=settings,
        )
    except ValueError as exc:
        raise ApiError(400, "validation_error", f"Не удалось обработать изображение: {exc}") from exc

    _record_photo_scan(db, principal, len(data), result, best_effort=False)

    # Задача тимлида 22.09 (reports/devops-stand-vlm.md, находка "источник нигде не
    # виден") — result.text_source/label_text (CV_FUSION, app/cv/service.py) в HTTP-ответ
    # НЕ идут (контракт не меняется), только в лог, только источник/длина/время — без
    # содержимого текста и без ключей шлюза. text_source is None <=> CV_FUSION=0 (путь
    # без чтения этикетки моделью/OCR) — логировать нечего. Только rich-ветка: flat и
    # /v1/eval/predict (общий flat_scan_response выше) — приватная выборка кейсодержателя,
    # тот же принцип, что архив ниже её не пишет (contracts/image-scan.md v0.4.10).
    if result.text_source is not None:
        logger.info(
            "scan_photo: текст этикетки прочитан source=%s len=%d ms=%d",
            result.text_source, len(result.label_text or ""), result.timing_ms,
        )

    # v0.4.10: архив сканов для контрольной выборки — только rich (интерфейс); flat и
    # /v1/eval/predict не архивируются: через них идёт приватная выборка кейсодержателя.
    # Фоновая задача выполняется после отправки ответа и не влияет на время скана.
    if settings.scan_archive_dir:
        background_tasks.add_task(
            archive_scan_safe, settings.scan_archive_dir, data, result,
            abs_floor=settings.cv_abs_floor,
            index_version=getattr(image_index, "index_version", None),
        )

    return ScanPhotoRichResponse(
        slug=result.slug,
        card=result.card,
        confidence=_build_confidence(settings, result),
        ocr_verified=result.ocr_verified,
        timing_ms=result.timing_ms,
        not_in_catalog=result.not_in_catalog,
        # Безымянный аналог не рендерится карточкой — отбрасываем до схемы
        # (см. комментарий у AnalogsWineItem: у единичных вин каталога name=None).
        similar=[AnalogsWineItem(**item) for item in result.similar if item.get("name")],
        analogs=[AnalogsWineItem(**item) for item in result.analogs if item.get("name")],
        # v0.4.3 (пробел нашёл F): top-5 сырых ANN-позиций для eval F1-top5,
        # UI не рендерит (см. докстринг PhotoMatchItem/ScanPhotoRichResponse).
        matches=[PhotoMatchItem(**item) for item in result.matches],
        # v0.4.11: top-5 кандидатов, обогащённых карточкой ("Возможно, это
        # одно из:" при not_in_catalog) — см. докстринг ScanCandidateItem.
        candidates=[ScanCandidateItem(**item) for item in result.candidates],
    )
