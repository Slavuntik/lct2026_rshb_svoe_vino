"""FastAPI-приложение «Свой Сомелье». `uvicorn app.main:app` поднимается без
единого env-параметра: LLM_PROVIDER и RAG_PROVIDER по умолчанию "mock",
DATABASE_URL по умолчанию файловый SQLite рядом с процессом (см. config.py).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from llm.base import get_llm

from .config import get_settings
from .cv.factory import get_image_index, get_label_verifier, warm_up_image_index, warm_up_label_verifier
from .db import make_engine, make_session_factory
from .errors import register_error_handlers
from .models import Base
from .rag.factory import get_retriever
from .routers import (
    analogs,
    auth,
    case_thumbs,
    chat,
    consents,
    eval as eval_router,
    events,
    health,
    metrics,
    profile,
    scan,
    taste,
    waitlist,
    wines,
)

API_PREFIX = "/v1"

# Задача тимлида 22.09 (reports/backend-swagger.md): nginx стенда
# (infra/ams3/nginx-somelye.conf) проксирует в API только /v1/ — старые
# дефолтные /docs, /redoc, /openapi.json снаружи попадают в SPA-фоллбэк
# nginx (location /) и отдают index.html вместо Swagger. Все три пути ниже
# — под /v1, чтобы жюри и команда могли открыть документацию прямо со
# стенда: http://89.110.72.101/v1/docs.
DOCS_URL = "/v1/docs"
REDOC_URL = "/v1/redoc"
OPENAPI_URL = "/v1/openapi.json"

# Версия дублирует contracts/openapi.yaml (поле version верхнего уровня) —
# синхронизируется вручную, контракт правит только architect (ORCHESTRATION.md).
API_VERSION = "0.3.3"

API_DESCRIPTION = """\
API «Свой Сомелье» — сканер российских вин по фото (кейс №10 РСХБ, хакатон ЛЦТ 2026).

### Авторизация
Большинству маршрутов нужен Bearer-токен принципала (гость или зарегистрированный
пользователь). Быстрее всего получить гостевой:

```
POST /v1/auth/guest
{"age_confirmed": true, "consent_version": "<версия текущего согласия>"}
```

Ответ содержит `access_token`. Нажмите **Authorize** вверху страницы и вставьте туда
только сам токен, без слова `Bearer` — Swagger добавит префикс сам.

### Сканер этикетки
- `POST /v1/eval/predict` — БЕЗ токена, фиксированный маршрут офлайн-скрипта проверки
  кейсодержателя: плоский ответ `{"slug": "..."}`, HTTP 200 при любом внутреннем сбое.
- `POST /v1/scan/photo` — тот же движок, полный ответ (карточка вина, кандидаты,
  аналоги); токен опционален и только атрибутирует скан пользователю.

Протокол сканера целиком — `contracts/image-scan.md`; карточка вина и гастропары
после скана — `contracts/post-scan.md`.
"""

BEARER_DESCRIPTION = (
    "JWT принципала (гость или пользователь), полученный через `POST /v1/auth/guest`, "
    "`POST /v1/auth/register` или `POST /v1/auth/login`. В диалоге Authorize вставьте "
    "только сам токен — без слова \"Bearer\", Swagger добавит его сам."
)

# Проверено на маршрутах (grep "tags=" по app/routers/*.py, 22.09) — бриф тимлида
# называл часть тегов (health/auth/scan/wines/sommelier/taste/profile), но реально
# в коде используется более широкий и слегка другой набор ("sommelier" — это тег
# "chat"), ниже описаны ВСЕ теги, которые действительно стоят на роутерах, а не
# только перечисленные в брифе.
tags_metadata = [
    {"name": "health", "description": "Живость и готовность сервиса: версии индексов RAG/CV, статус прогрева при старте (`GET /v1/healthz`)."},
    {"name": "auth", "description": "Регистрация, вход и гостевой доступ. Гость получает токен через `POST /v1/auth/guest` без пароля (нужно подтверждение 18+); тот же токен можно доапгрейдить до полной регистрации `POST /v1/auth/register`."},
    {"name": "consents", "description": "Согласия пользователя (18+, обработка данных, профилирование вкуса) — чтение и запись по scope."},
    {"name": "scan", "description": "Сканер этикетки по фото: `POST /v1/scan/photo` — основной путь с полным ответом (карточка вина, кандидаты, аналоги); `POST /v1/scan/resolve` — резолв по названию; `POST /v1/scan/ocr` зарезервирован под веб-фолбэк и всегда отвечает 501."},
    {"name": "eval", "description": "`POST /v1/eval/predict` — фиксированный алиас `/v1/scan/photo?flat=1` для офлайн-скрипта проверки кейсодержателя: без токена, плоский ответ `{\"slug\": \"...\"}`, HTTP 200 при любом внутреннем сбое."},
    {"name": "wines", "description": "Карточка вина по идентификатору, включая гастрономические пары."},
    {"name": "chat", "description": "Чат с сомелье (RAG + LLM, потоковый ответ по SSE) и обратная связь по репликам."},
    {"name": "analogs", "description": "Подбор российского аналога импортного вина по сорту/стилю — детерминированно, без обращения к LLM."},
    {"name": "taste", "description": "Паспорт вкуса: свайп-дегустация, профиль и кандидаты для дегустации. Нужно согласие scope=profiling — гостю недоступно."},
    {"name": "profile", "description": "Экспорт данных пользователя и удаление аккаунта (soft-delete). Доступно только зарегистрированным — не гостям."},
    {"name": "events", "description": "Приём продуктовой аналитики с клиента — единственная точка записи событий на сервере."},
    {"name": "waitlist", "description": "Лендинг: запись email в лист ожидания, без авторизации."},
    {"name": "metrics", "description": "Публичная сводка последнего офлайн-прогона точности сканера — для питча и демо на стенде."},
    {"name": "case-thumbs", "description": "Статическая раздача превью эталонов каталога для фолбэк-карточки сканера."},
]


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Свой Сомелье API",
        version=API_VERSION,
        description=API_DESCRIPTION,
        docs_url=DOCS_URL,
        redoc_url=REDOC_URL,
        openapi_url=OPENAPI_URL,
        openapi_tags=tags_metadata,
    )
    app.state.settings = settings

    def custom_openapi() -> dict:
        # Кэш как в дефолтной реализации FastAPI.openapi() — пересобирать схему на
        # каждый запрос /v1/openapi.json незачем, а app.openapi_schema сбрасывается
        # в None при каждом create_app() (новый объект app).
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=tags_metadata,
        )
        # В проекте нет ни одной fastapi.security-схемы (auth проверяется вручную
        # через заголовок Authorization, см. app/security.py) — Swagger поэтому не
        # рисовал кнопку Authorize вообще. BearerAuth ниже — чисто описательная
        # схема для /v1/docs: она не меняет рантайм-проверку токена (та осталась в
        # security.py), только даёт Swagger UI показать Authorize и подставлять
        # введённый токен в заголовок Authorization на "Try it out" по всем путям.
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": BEARER_DESCRIPTION,
        }
        schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi

    engine = make_engine(settings)
    Base.metadata.create_all(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.retriever = get_retriever(settings)
    app.state.llm = get_llm()
    app.state.image_index = get_image_index(settings)
    app.state.label_verifier = get_label_verifier(settings)
    # Ревью 04, блокер 2: прогрев ПРИ СТАРТЕ процесса, не на первом боевом
    # запросе (холодный старт реального SigLIP2 занял ~340 с у F2) — /healthz
    # сообщает результат в поле warm.
    app.state.image_index_warm = warm_up_image_index(app.state.image_index, settings)
    # v0.4.7 (контракт §5, TODO-1 ревью 05): та же дисциплина для LabelVerifier
    # (PaddleOCR тоже ленивая загрузка) — репетиция B3 намерила +2 с первому
    # боевому near-dup запросу без этого прогрева (c03edd0). /healthz.warm —
    # AND обоих прогревов (app/routers/health.py).
    app.state.label_verifier_warm = warm_up_label_verifier(app.state.label_verifier, settings)

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
        eval_router.router,
        wines.router,
        chat.router,
        analogs.router,
        events.router,
        taste.router,
        waitlist.router,
        profile.router,
        metrics.router,
        case_thumbs.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()
