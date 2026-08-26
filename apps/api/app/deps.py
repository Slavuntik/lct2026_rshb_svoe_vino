"""Зависимости на синглтоны приложения (ретривер, LLM), зафиксированные в
app.state в create_app() — по аналогии с get_db/get_settings_dep."""
from __future__ import annotations

from fastapi import Request
from llm.base import LLM

from .rag.interface import Retriever


def get_retriever_dep(request: Request) -> Retriever:
    return request.app.state.retriever


def get_llm_dep(request: Request) -> LLM:
    return request.app.state.llm
