"""Задача тимлида 22.09 (reports/backend-swagger.md): nginx стенда
(infra/ams3/nginx-somelye.conf) проксирует в API только /v1/ — дефолтные
/docs, /redoc, /openapi.json снаружи попадают в SPA-фоллбэк nginx и отдают
index.html, а не Swagger. app/main.py переносит все три под /v1
(docs_url="/v1/docs", redoc_url="/v1/redoc", openapi_url="/v1/openapi.json")
— этот файл фиксирует новые адреса и то, что старые дефолтные пути больше
ничего не отдают (404, а не случайный SPA-успех).
"""
from __future__ import annotations

from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from app.main import API_PREFIX
from app.routers import (
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

# Тот же список роутеров, что и цикл app.include_router(...) в app/main.py —
# независимый от самой OpenAPI-схемы источник истины: этот пакет FastAPI
# (0.141.1) больше не разворачивает include_router() в плоский список
# APIRoute на app.routes (там теперь fastapi.routing._IncludedRouter,
# приватная обёртка) — путь снизу собирается вручную из router.prefix +
# route.path, ровно как это делает сам FastAPI при инклюде.
_ROUTER_MODULES = (
    health, auth, consents, scan, eval_router, wines, pairing, chat,
    analogs, events, taste, waitlist, profile, metrics, case_thumbs, catalog,
)


def _expected_v1_paths() -> set[str]:
    paths: set[str] = set()
    for module in _ROUTER_MODULES:
        for route in module.router.routes:
            # route.path на уровне самого APIRouter уже несёт его собственный
            # prefix (например "/auth/guest" у app/routers/auth.py) — этот
            # prefix применяется к маршруту сразу при регистрации на router,
            # а не только при app.include_router(). Остаётся добавить только
            # внешний API_PREFIX ("/v1"), который навешивает main.py.
            if isinstance(route, APIRoute):
                paths.add(API_PREFIX + route.path)
    return paths


def test_v1_docs_serves_swagger_ui_html(client: TestClient):
    r = client.get("/v1/docs")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "swagger-ui" in r.text.lower()


def test_v1_redoc_serves_html(client: TestClient):
    r = client.get("/v1/redoc")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_v1_openapi_json_contains_every_registered_v1_route(client: TestClient):
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")

    schema = r.json()
    assert schema["info"]["title"] == "Свой Сомелье API"
    assert schema["info"]["version"] == "0.3.6"

    schema_paths = set(schema["paths"])
    # Истина — пути, собранные напрямую из модулей роутеров (не сама схема,
    # иначе проверка была бы тавтологией): каждый путь приложения обязан быть
    # под /v1 и присутствовать в JSON, который реально уходит по HTTP.
    app_paths = _expected_v1_paths()
    assert app_paths, "не собралось ни одного ожидаемого пути — тест сломан, а не приложение"
    assert all(p.startswith("/v1/") for p in app_paths), (
        f"в списке роутеров есть путь вне /v1/*: {sorted(p for p in app_paths if not p.startswith('/v1/'))}"
    )
    missing = sorted(app_paths - schema_paths)
    assert not missing, f"в /v1/openapi.json не хватает зарегистрированных маршрутов: {missing}"
    assert all(p.startswith("/v1/") or p == "/v1" for p in schema_paths), (
        f"в схеме /v1/openapi.json есть путь вне /v1/*: {sorted(p for p in schema_paths if not p.startswith('/v1'))}"
    )


def test_v1_openapi_json_has_described_bearer_security_scheme(client: TestClient):
    """Без этого Authorize в Swagger UI не появляется вовсе (в проекте нет ни
    одной fastapi.security-схемы — auth разобран вручную, app/security.py) —
    см. custom_openapi() в app/main.py."""
    schema = client.get("/v1/openapi.json").json()
    bearer = schema["components"]["securitySchemes"]["BearerAuth"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    assert bearer["description"]


def test_default_docs_paths_are_gone_after_move_to_v1(client: TestClient):
    """Критично для стенда: nginx-somelye.conf проксирует в API только /v1/,
    поэтому голые /docs и /openapi.json обязаны 404 у самого приложения
    (не 200 с чем-то отличным от SPA — снаружи их и так перехватит nginx,
    но приложение не должно тихо продолжать их обслуживать)."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
