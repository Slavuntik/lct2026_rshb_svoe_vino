"""FastAPI-приложение «Свой Сомелье». `uvicorn app.main:app` поднимается без
единого env-параметра: LLM_PROVIDER и RAG_PROVIDER по умолчанию "mock",
DATABASE_URL по умолчанию файловый SQLite рядом с процессом (см. config.py).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from llm.base import get_llm

from .config import get_settings
from .db import make_engine, make_session_factory
from .errors import register_error_handlers
from .models import Base
from .rag.factory import get_retriever
from .routers import (
    analogs,
    auth,
    chat,
    consents,
    events,
    health,
    profile,
    scan,
    taste,
    waitlist,
    wines,
)

API_PREFIX = "/v1"


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title="Svoy Somelye API", version="0.2.0")
    app.state.settings = settings

    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.retriever = get_retriever(settings)
    app.state.llm = get_llm()

    # v0.3 (ревью 02, п.6): "оживить" cors_origins — раньше поле в Settings
    # существовало, но никто его не читал. Bearer-токены в Authorization,
    # не куки => allow_credentials не нужен (и опаснее сочетать с "*").
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    for router in (
        health.router,
        auth.router,
        consents.router,
        scan.router,
        wines.router,
        chat.router,
        analogs.router,
        events.router,
        taste.router,
        waitlist.router,
        profile.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()
