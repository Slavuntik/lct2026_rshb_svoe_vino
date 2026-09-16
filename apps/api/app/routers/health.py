from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..deps import get_retriever_dep
from ..rag.interface import Retriever
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(request: Request, retriever: Retriever = Depends(get_retriever_dep)) -> HealthResponse:
    index_version = getattr(retriever, "index_version", "unknown")
    # v0.4.4 (ревью 04, блокер 2): image_index_warm выставляется один раз при
    # старте (app/main.py::create_app() -> warm_up_image_index) — getattr на
    # случай контекста без полного create_app() (по умолчанию True, как и
    # сам флаг для IMAGE_PROVIDER=mock).
    warm = getattr(request.app.state, "image_index_warm", True)
    return HealthResponse(status="ok", index_version=index_version, warm=warm)
