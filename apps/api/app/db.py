"""Движок SQLAlchemy + сессии.

Источник истины по схеме — contracts/schema.sql (Postgres). В дев-среде нет
Postgres, поэтому здесь SQLite-совместимый слой; расхождения перечислены в
apps/api/app/models.py (докстринг модуля) и в reports/b-report.md.

Engine/sessionmaker создаются один раз на приложение в create_app() и лежат
в app.state — так тесты могут поднимать независимые приложения с независимыми
БД (файл на tmp_path или in-memory) без обращения к глобальному состоянию
модуля.
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings


def make_engine(settings: Settings) -> Engine:
    kwargs: dict = {}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in settings.database_url:
            # Каждый чекаут из пула иначе может попасть на новый поток (FastAPI
            # гоняет sync-эндпоинты через anyio threadpool) и получить СВОЙ
            # пустой :memory: — StaticPool держит одно соединение на весь engine.
            kwargs["poolclass"] = StaticPool
    engine = create_engine(settings.database_url, **kwargs)

    if settings.database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection, connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
