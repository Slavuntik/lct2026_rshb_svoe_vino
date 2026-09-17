"""GET /v1/healthz — v0.4.7 (контракт §6, TODO-3 ревью 05): версии разведены
по пространствам имён (rag_index_version / cv_index_version), `index_version`
остаётся deprecated-алиасом rag_index_version (до v0.5, contracts/openapi.yaml).

До этой правки поле `index_version` было ОДНО и всегда смотрело на RAG-ретривер
— ревью 05 поймало на этом ложную тревогу при прогоне B3 (RAG_PROVIDER=mock,
IMAGE_PROVIDER=real): index_version показывал "mock-fixtures-..." и это
прочиталось как "CV тоже на моке", хотя реальный CV-индекс работал —
опровергнуто логом qdrant "49650 points" (см. reports/05-dataset-wave.md,
"образцовое поведение"). Разведение версий убирает саму возможность этой
путаницы: rag_index_version и cv_index_version — два разных, всегда честных
поля, ни одно не маскирует другое.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..cv.interface import ImageIndex
from ..deps import get_image_index_dep, get_retriever_dep
from ..rag.interface import Retriever
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(
    request: Request,
    retriever: Retriever = Depends(get_retriever_dep),
    image_index: ImageIndex = Depends(get_image_index_dep),
) -> HealthResponse:
    rag_index_version = getattr(retriever, "index_version", "unknown")
    # cv.index.ImageIndex.index_version (реальный провайдер) честно отдаёт
    # None, пока индекс ни разу не строился (см. app/cv/interface.py) —
    # openapi.yaml объявляет cv_index_version как строку (не nullable),
    # поэтому коалесцируем в тот же "unknown"-сентинел, что и у rag выше,
    # а не протаскиваем null наружу в поле контрактно-строкового типа.
    cv_index_version = getattr(image_index, "index_version", None) or "unknown"
    # v0.4.4 (ревью 04, блокер 2) + v0.4.7 (TODO-1, прогрев верификатора):
    # image_index_warm/label_verifier_warm выставляются один раз при старте
    # (app/main.py::create_app() -> warm_up_image_index/warm_up_label_verifier)
    # — getattr на случай контекста без полного create_app() (по умолчанию
    # True для обоих, как и сами флаги на mock-провайдерах). `warm` — AND
    # обоих: сервис честно "не тёплый", если хотя бы один прогрев не прошёл.
    warm = (
        getattr(request.app.state, "image_index_warm", True)
        and getattr(request.app.state, "label_verifier_warm", True)
    )
    return HealthResponse(
        status="ok",
        index_version=rag_index_version,
        rag_index_version=rag_index_version,
        cv_index_version=cv_index_version,
        warm=warm,
    )
