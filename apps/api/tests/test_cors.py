"""v0.3 (ревью 02, п.6): cors_origins реально применяется через CORSMiddleware
— дефолт покрывает localhost dev-порты Vite (apps/web, server.port=5173)."""
from __future__ import annotations

from starlette.testclient import TestClient


def test_cors_allows_configured_vite_dev_origin(client: TestClient):
    r = client.get("/v1/healthz", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_allows_vite_dev_origin(client: TestClient):
    r = client.options("/v1/healthz", headers={
        "Origin": "http://127.0.0.1:5173",
        "Access-Control-Request-Method": "GET",
    })
    assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_cors_does_not_allow_arbitrary_origin(client: TestClient):
    r = client.get("/v1/healthz", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200  # запрос всё равно обслуживается (не same-origin блокировка сервера)
    assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_cors_origins_are_configurable_via_env(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("CORS_ORIGINS", "https://svoy-somelye.example")
    client = TestClient(create_app())

    r = client.get("/v1/healthz", headers={"Origin": "https://svoy-somelye.example"})
    assert r.headers.get("access-control-allow-origin") == "https://svoy-somelye.example"

    r = client.get("/v1/healthz", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") != "http://localhost:5173"
