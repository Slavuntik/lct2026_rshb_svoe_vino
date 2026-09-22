from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import create_app
from app.ratelimit import reset_rate_limits


@pytest.fixture(autouse=True)
def _reset_cv_fusion_model_breakers():
    """Тимлид 22.09 (расширение брифа scan-budget, п.8): предохранители VLM-моделей
    (`app.cv.service._MODEL_BREAKERS`) живут НА ПРОЦЕСС (модульный словарь, не
    per-app/per-Settings) — без сброса тест, открывший предохранитель (несколько
    сбоев/таймаутов подряд), тихо влияет на ПОСЛЕДУЮЩИЕ тесты в том же прогоне
    pytest, даже не связанные с CV_FUSION. Autouse — дешёвая проверка двух
    словарных полей, не задевает тесты, которые предохранители не касаются."""
    from app.cv.service import _reset_model_breakers

    _reset_model_breakers()
    yield
    _reset_model_breakers()


@pytest.fixture()
def app(monkeypatch):
    """Свежее приложение на изолированной in-memory БД, mock-LLM и mock-RAG.

    LLM_PROVIDER/RAG_PROVIDER не выставляем явно — дефолт в app/config.py уже
    "mock", и на этом как раз держится DoD "uvicorn поднимается одной
    командой без единого ключа". Здесь эти же дефолты просто используются как
    есть, а не переопределяются, чтобы тест бил по тому же пути, что и dev-запуск.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1000")  # тесты не должны спотыкаться о лимитер
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    reset_rate_limits()
    return create_app()


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


def register_user(client: TestClient, *, email: str, password: str = "password123",
                   birth_date: str = "1990-01-01", scopes: list[str] | None = None,
                   consent_version: str = "2026-01-01.1") -> dict:
    r = client.post("/v1/auth/register", json={
        "email": email, "password": password, "birth_date": birth_date,
        "consent_version": consent_version, "consent_scopes": scopes or ["base", "profiling"],
    })
    assert r.status_code == 201, r.text
    return r.json()


def auth_header(token_pair: dict) -> dict:
    return {"Authorization": f"Bearer {token_pair['access_token']}"}


def make_guest(client: TestClient, *, consent_version: str = "2026-01-01.1") -> dict:
    r = client.post("/v1/auth/guest", json={"age_confirmed": True, "consent_version": consent_version})
    assert r.status_code == 201, r.text
    return r.json()
