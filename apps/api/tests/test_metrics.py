"""GET /v1/metrics/scan (contracts/image-scan.md v0.4)."""
from __future__ import annotations

import json

from starlette.testclient import TestClient


def test_metrics_scan_no_eval_report_yet_returns_honest_nulls(client: TestClient):
    r = client.get("/v1/metrics/scan")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "index_version", "f1_top1", "f1_top5", "match_rate", "eval_set", "measured_at",
    }
    assert body["f1_top1"] is None
    assert body["f1_top5"] is None
    assert body["match_rate"] is None
    # index_version по-прежнему известен — это свойство живого индекса, не eval-отчёта.
    assert body["index_version"] == "mock-cv-fixtures-0.1"


def test_metrics_scan_no_auth_required(client: TestClient):
    r = client.get("/v1/metrics/scan")
    assert r.status_code == 200


def test_metrics_scan_reads_real_eval_report_file(monkeypatch, tmp_path):
    from app.main import create_app

    report = {
        "index_version": "cv-2026.09.15.1",
        "f1_top1": 0.94,
        "f1_top5": 0.99,
        "match_rate": 0.92,
        "eval_set": "public-field-photos-v1",
        "measured_at": "2026-09-15T12:00:00Z",
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("CV_EVAL_REPORT_PATH", str(report_path))
    client = TestClient(create_app())

    r = client.get("/v1/metrics/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["index_version"] == "cv-2026.09.15.1"
    assert body["f1_top1"] == 0.94
    assert body["f1_top5"] == 0.99
    assert body["match_rate"] == 0.92
    assert body["eval_set"] == "public-field-photos-v1"
    assert body["measured_at"] == "2026-09-15T12:00:00Z"


def test_scan_photo_rich_confidence_picks_up_f1_from_eval_report(monkeypatch, tmp_path):
    from app.main import create_app

    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"f1_top1": 0.9, "f1_top5": 0.97}), encoding="utf-8")

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("CV_EVAL_REPORT_PATH", str(report_path))
    client = TestClient(create_app())

    r = client.post(
        "/v1/scan/photo",
        files={"image": ("x.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    conf = r.json()["confidence"]
    assert conf["f1_top1"] == 0.9
    assert conf["f1_top5"] == 0.97
    assert conf["eval_missing"] is False
