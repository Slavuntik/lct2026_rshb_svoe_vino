from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_retriever_dep
from ..rag.interface import Retriever
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz(retriever: Retriever = Depends(get_retriever_dep)) -> HealthResponse:
    index_version = getattr(retriever, "index_version", "unknown")
    return HealthResponse(status="ok", index_version=index_version)
