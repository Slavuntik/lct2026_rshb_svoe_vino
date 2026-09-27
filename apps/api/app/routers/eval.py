"""POST /v1/eval/predict — эндпоинт скрипта кейсодержателя.

Источник требований: `case-data/eval/participant_test.sh` (+ README.md
рядом) — офлайн-скрипт организатора ЛЦТ шлёт фото по одному на
`--endpoint` (дефолт СКРИПТА: `http://127.0.0.1:8080/v1/eval/predict`),
multipart-поле `image`, curl `--connect-timeout 5 --max-time 10`, БЕЗ
Authorization-заголовка, последовательно, без ретраев. Принимает ответ
`{"slug": "..."}` ИЛИ `[{"slug": "..."}]` (jq в скрипте берёт `.slug` либо
`.[0].slug`); любой другой HTTP-код или форма — `predicted_slug=null`
(честный промах в predictions.jsonl, но не сбой прогона скрипта — он просто
продолжает со следующей строкой).

Этот путь — ФИКСИРОВАННЫЙ алиас плоского режима `/v1/scan/photo?flat=1`
(contracts/image-scan.md v0.4, "Режимы ответа API" + v0.4.4 "несгораемость
flat ДО КОНЦА"): ровно `{"slug": "wine-slug"}`, лучший доступный кандидат
даже при низкой уверенности, HTTP 200 при ЛЮБОМ внутреннем сбое. В отличие
от `/scan/photo`, здесь нет `?flat=1`/`SCAN_FLAT_DEFAULT` развилки — путь
сам по себе ВСЕГДА flat, потому что скрипт кейсодержателя не умеет
добавлять query-параметры и его формат менять нельзя (agents/B3-eval-route.md).

Общий обработчик с `/scan/photo`, БЕЗ копипасты: `routers/scan.py::
flat_scan_response()` — та же функция, тот же контракт несгораемости, тот
же приём "первое файловое поле независимо от имени". Разница только в
маршруте и в том, что здесь flat — не опция, а единственное поведение.

Без авторизации (как и `/scan/photo`) — скрипт не шлёт Bearer-токен вообще
(участник тестирует сервис анонимно, `case-data/eval/README.md` не
упоминает auth), `get_current_principal_optional` тут просто на случай,
если кто-то всё же продублирует вызов с токеном приложения — тогда скан
атрибутируется, как обычно, но это не требование скрипта.

reports/b3-eval-route.md, "Предложения к контрактам": сам путь
`/v1/eval/predict` пока не упомянут буквально в contracts/image-scan.md
(тот описывает только `/scan/photo`) — правка контракта вне зоны B3
(ORCHESTRATION.md: контракты меняет только оркестратор), см. отчёт.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from ..config import Settings, get_settings_dep
from ..cv.interface import ImageIndex, LabelVerifier
from ..db import get_db
from ..deps import get_image_index_dep, get_label_verifier_dep, get_retriever_dep
from ..rag.interface import Retriever
from ..schemas import ScanPhotoFlatResponse
from ..security import Principal, get_current_principal_optional
from .scan import flat_scan_response

router = APIRouter(prefix="/eval", tags=["eval"])


@router.post("/predict", response_model=ScanPhotoFlatResponse)
async def eval_predict(
    request: Request,
    image: UploadFile | None = File(default=None),
    principal: Principal | None = Depends(get_current_principal_optional),
    image_index: ImageIndex = Depends(get_image_index_dep),
    verifier: LabelVerifier = Depends(get_label_verifier_dep),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
    db: Session = Depends(get_db),
) -> ScanPhotoFlatResponse:
    return await flat_scan_response(
        request, image, principal, image_index, verifier, retriever, settings, db,
    )
