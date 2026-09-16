"""GET /v1/healthz — v0.4.4 (ревью 04, блокер 2) добавила поле `warm`."""
from __future__ import annotations

from starlette.testclient import TestClient


def test_healthz_reports_ok_status_and_index_version(client: TestClient):
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "index_version", "warm"}
    assert body["status"] == "ok"
    assert body["index_version"]


def test_healthz_warm_is_true_on_default_mock_image_provider(client: TestClient):
    """IMAGE_PROVIDER=mock (дефолт) — нечего греть, warm=True сразу, без
    обращения к настоящему энкодеру."""
    r = client.get("/v1/healthz")
    assert r.json()["warm"] is True
