"""Smoke-тест реальной связки B+A (contracts/rag-interface.md, DoD B п.7):
поднимает API с RAG_PROVIDER=real, RAG_MODE=embedded поверх собранного
индекса агента A и проходит /scan/resolve и /chat на настоящих данных
каталога vines — не на 6 вымышленных фикстурах.

Скип по умолчанию, включается ТРЕМЯ условиями одновременно:
  1. env RUN_RAG_INTEGRATION=1 (явное намерение — тяжёлые модели, не CI-дефолт);
  2. пакет rag импортируется (uv sync --extra dev --extra integration в apps/api
     — extra "integration" объявлена в pyproject.toml именно ради этого файла,
     не тянется в обычный `uv sync --extra dev`);
  3. собранный индекс существует (packages/rag/data/manifest.json — `rag ingest`
     агента A уже прогнан на этой машине).
Обычный `pytest -q` не видит ни одного из условий и просто пропускает файл.

LLM здесь по-прежнему mock — задача не про реальные LLM-ключи, а про то, что
B (API/промпт/цитаты) и A (retrieval) реально стыкуются по контракту на
настоящих данных, а не только на 6 фикstурах друг напротив друга.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

RAG_DATA_DIR = Path(__file__).resolve().parents[3] / "packages" / "rag" / "data"
MANIFEST_PATH = RAG_DATA_DIR / "manifest.json"

_RUN_FLAG = os.environ.get("RUN_RAG_INTEGRATION") == "1"

try:
    import rag  # noqa: F401  — наличие проверяем самим импортом, не только флагом
    _RAG_IMPORTABLE = True
except ImportError:
    _RAG_IMPORTABLE = False

_INDEX_BUILT = MANIFEST_PATH.exists()
_READY = _RUN_FLAG and _RAG_IMPORTABLE and _INDEX_BUILT

_SKIP_REASON = (
    "integration выключен по умолчанию — нужны все три: RUN_RAG_INTEGRATION=1 "
    f"({'ok' if _RUN_FLAG else 'нет'}), пакет rag установлен "
    f"({'ok' if _RAG_IMPORTABLE else 'нет — uv sync --extra integration'}), "
    f"собранный индекс {MANIFEST_PATH} ({'ok' if _INDEX_BUILT else 'нет — rag ingest'})"
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def real_rag_client(monkeypatch):
    # Импорт create_app() ЗДЕСЬ, не на уровне модуля: RAG_PROVIDER читается
    # один раз в create_app(), должен быть выставлен до вызова.
    from app.main import create_app

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1000")
    monkeypatch.setenv("RAG_PROVIDER", "real")
    monkeypatch.setenv("RAG_MODE", "embedded")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)  # остаётся mock

    return TestClient(create_app())


def _guest_headers(client: TestClient) -> dict[str, str]:
    r = client.post("/v1/auth/guest", json={"age_confirmed": True, "consent_version": "v1"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_healthz_reports_manifest_index_version(real_rag_client):
    r = real_rag_client.get("/v1/healthz")
    assert r.status_code == 200
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert r.json()["index_version"] == manifest.get("version")


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_scan_resolve_finds_known_real_winery(real_rag_client):
    headers = _guest_headers(real_rag_client)
    r = real_rag_client.post("/v1/scan/resolve", json={"text": "Абрау-Дюрсо"}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["matches"], "известная винодельня каталога должна дать хоть один матч"
    assert 0 <= body["matches"][0]["confidence"] <= 1


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_chat_answers_generic_question_with_grounded_citation(real_rag_client):
    headers = _guest_headers(real_rag_client)
    r = real_rag_client.post(
        "/v1/chat", json={"message": "Какое красное вино выбрать к стейку?"}, headers=headers,
    )
    assert r.status_code == 200

    events = []
    for block in r.text.split("\n\n"):
        for line in block.strip().splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    types = [e["type"] for e in events]

    assert types, "пустой SSE-ответ"
    assert "refusal" not in types, f"общий вопрос на полном каталоге не должен рефьюзиться: {events[:3]}"
    assert types[-1] == "done"
    assert "citation" in types, "фактический ответ обязан нести хотя бы одну цитату"
