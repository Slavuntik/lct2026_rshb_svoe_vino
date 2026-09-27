"""FastAPI-приложение «Свой Сомелье». `uvicorn app.main:app` поднимается без
единого env-параметра: LLM_PROVIDER и RAG_PROVIDER по умолчанию "mock",
DATABASE_URL по умолчанию файловый SQLite рядом с процессом (см. config.py).
"""
from __future__ import annotations

import logging
import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from llm.base import get_llm

from .config import get_settings
from .cv.factory import get_image_index, get_label_verifier, warm_up_image_index, warm_up_label_verifier
from .db import make_engine, make_session_factory
from .dish_pairing import warm_up_catalog_cache
from .errors import register_error_handlers
from .models import Base
from .rag.factory import get_retriever, warm_up_retriever
from .routers import (
    analogs,
    auth,
    case_thumbs,
    catalog,
    chat,
    consents,
    eval as eval_router,
    events,
    health,
    metrics,
    pairing,
    profile,
    scan,
    taste,
    waitlist,
    wines,
)

API_PREFIX = "/v1"

# Решение тимлида 22.09 (reports/backend-chat-retrieval.md, п.4, поверх находки
# reports/backend-text-source.md): единый логгер пространства имён "app" — ОДИН
# handler+INFO на весь процесс здесь, а не точечные хендлеры по отдельным файлам
# (был один такой в routers/scan.py, убран этой же правкой). Голый `uvicorn
# app.main:app` (infra/ams3/somelye-api.service, infra/Dockerfile.api — без
# --log-level) НЕ трогает root: тот остаётся на Python-дефолте WARNING без
# хендлеров, и logging.lastResort тоже ловит только WARNING+ — INFO-записи
# любого модуля apps/api (app.routers.scan, app.chat.filters, ...) без этого
# не долетали бы никуда вообще (проверено эмпирически, см. reports/
# backend-text-source.md). Дочерние логгеры ("app.<модуль>") подхватывают этот
# handler через обычный propagate (Python-дефолт, не трогаем) — свой handler
# ставить не нужно больше нигде в apps/api. Модульный уровень (не внутри
# create_app()) + guard `if not _app_logger.handlers` — идемпотентно даже при
# многократном create_app() за один процесс (apps/api/tests/conftest.py зовёт
# create_app() на КАЖДЫЙ тест, сотни раз за прогон): без guard'а хендлеры
# копились бы и каждая INFO-запись печаталась бы N раз — ровно те дубли строк
# в выводе процесса, которых просил избежать тимлид. Модуль импортируется один
# раз на процесс (Python кэширует import) — де-факто это тоже гарантирует
# ровно один handler, guard — вторая, явная страховка на случай, если что-то
# в тестовом раннере всё же перевыполнит тело модуля.
_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _app_handler = logging.StreamHandler()
    _app_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _app_logger.addHandler(_app_handler)

logger = logging.getLogger(__name__)

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
# 0.3.3 -> 0.3.6 (22.09, backend, reports/backend-similar-wines.md): контракт
# получил similar_wines (GET /wines/{id}) и top_styles_named (GET
# /taste/profile) ЗАРАНЕЕ (architect ратифицировал 0.3.6 в рамках аудита
# reports/qa-manual-hack-v16.md), эта волна backend реализует оба поля —
# синхронизирую строку версии вместе с реальным поведением путей, тот же
# принцип, что и у v0.3.4 (см. историю contracts/openapi.yaml).
# 0.3.6 -> 0.3.7 (27.09): architect ратифицировал GET /v1/catalog (коммит
# 173c892, reports/architect-catalog-contract.md) — новая ручка, не довнесение
# поведения существующей; тимлид подтвердил, что бамп уместен.
API_VERSION = "0.3.7"

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
    {"name": "pairing", "description": "«Что подать» по фото блюда или по вручную выбранной категории: распознавание блюда и подбор вин из каталога/движка правил."},
    {"name": "chat", "description": "Чат с сомелье (RAG + LLM, потоковый ответ по SSE) и обратная связь по репликам."},
    {"name": "analogs", "description": "Подбор российского аналога импортного вина по сорту/стилю — детерминированно, без обращения к LLM."},
    {"name": "taste", "description": "Паспорт вкуса: свайп-дегустация, профиль и кандидаты для дегустации. Нужно согласие scope=profiling — гостю недоступно."},
    {"name": "profile", "description": "Экспорт данных пользователя и удаление аккаунта (soft-delete). Доступно только зарегистрированным — не гостям."},
    {"name": "events", "description": "Приём продуктовой аналитики с клиента — единственная точка записи событий на сервере."},
    {"name": "waitlist", "description": "Лендинг: запись email в лист ожидания, без авторизации."},
    {"name": "metrics", "description": "Публичная сводка последнего офлайн-прогона точности сканера — для питча и демо на стенде."},
    {"name": "case-thumbs", "description": "Статическая раздача превью эталонов каталога для фолбэк-карточки сканера."},
    {"name": "catalog", "description": "Каталог вин кейса (2103 позиции) — постраничная плитка для главного экрана: поиск по названию/винодельне, фильтры цвет/сахар."},
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
    # Дополнение тимлида 22.09 (reports/backend-chat-retrieval.md, п.6): та же
    # дисциплина для RAG-ретривера (packages/rag/rag/embeddings.py::DenseEmbedder,
    # fastembed) — ленивая загрузка на первый search() заняла ~8 с на тех же 4 vCPU
    # стенда, что и сканер, и совпала по времени с прогонами 100 фото, дав им хвост
    # 7-9 с (reports/devops-hack-v13.md). Прогрев осознанно НЕ пишет в поле,
    # участвующее в GET /healthz.warm ("healthz.warm трогать не нужно" — прямое
    # указание тимлида, см. app/rag/factory.py::warm_up_retriever) — только в лог,
    # тем же логгером "app" (см. блок настройки выше), время прогрева пишем сами
    # (warm_up_retriever() возвращает только bool успеха/неудачи).
    _rag_warmup_t0 = time.monotonic()
    _rag_warm = warm_up_retriever(app.state.retriever, settings)
    _rag_warmup_s = time.monotonic() - _rag_warmup_t0
    logger.info("retriever warm-up: warm=%s, %.3f s", _rag_warm, _rag_warmup_s)

    # Тимлид 22.09 (после замера "холодный кэш 12 с на 2103 карточки",
    # reports/backend-dish-photo.md): "не должен доставаться первому
    # пользователю — на демо это выглядит как зависший запрос". В ОТЛИЧИЕ от
    # прогревов выше (синхронные — секунды, не десятки) — 12 с достаточно
    # долго, чтобы задерживать готовность ВСЕГО сервиса ради этого было бы
    # хуже, чем не прогревать вовсе: фоновый поток, create_app() возвращается
    # сразу же. Сбой прогрева НЕ роняет старт (try/except внутри
    # warm_up_catalog_cache()) — кэш тогда просто соберётся лениво на первом
    # запросе `/v1/pairing/*`, как и без прогрева.
    #
    # 22.09 (находка при полном прогоне тестов): `case_catalog.py` читает
    # `CASE_DATA_DIR` "живьём" при каждом вызове — намеренно, чтобы тесты
    # могли `monkeypatch.setenv()` ПОСЛЕ create_app() (см. её докстринг). Фоновый
    # поток читает этот же env НЕ синхронно с созданием приложения — под pytest,
    # где `create_app()` зовётся СОТНИ раз за прогон с быстро сменяющимся
    # `CASE_DATA_DIR`, поток одного теста может дочитать до env УЖЕ следующего
    # теста и заразить его process-wide кэш `case_catalog._load_catalog`
    # (поймано не гипотетически — 4 теста test_wine_pairings.py стабильно падали
    # при полном прогоне apps/api). В проде `create_app()` вызывается РОВНО ОДИН
    # раз за жизнь процесса — гонки с "следующим тестом" структурно нет, поэтому
    # прогрев пропускается только под pytest (`PYTEST_CURRENT_TEST` — офиц.
    # флаг pytest ровно для этого случая, docs: "запущен ли код как часть
    # теста"), не по общему признаку окружения. Кэш тестам, которым он нужен,
    # либо подменяют `_iter_catalog_cards()` напрямую, либо (в паре тестов
    # роутера) собирается лениво на первый реальный запрос — оба пути уже
    # покрыты тестами независимо от прогрева.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        def _warm_up_dish_pairing_catalog_in_background() -> None:
            _t0 = time.monotonic()
            _warm = warm_up_catalog_cache(app.state.retriever)
            logger.info("dish-pairing catalog warm-up: warm=%s, %.3f s", _warm, time.monotonic() - _t0)

        threading.Thread(
            target=_warm_up_dish_pairing_catalog_in_background,
            name="dish-pairing-catalog-warmup", daemon=True,
        ).start()

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
        pairing.router,
        chat.router,
        analogs.router,
        events.router,
        taste.router,
        waitlist.router,
        profile.router,
        metrics.router,
        case_thumbs.router,
        catalog.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()
