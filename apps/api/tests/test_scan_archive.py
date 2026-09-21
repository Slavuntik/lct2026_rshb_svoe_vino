"""Архив сканов стенда (contracts/image-scan.md v0.4.10, SCAN_ARCHIVE_DIR)."""
from __future__ import annotations

import io
import json

import pytest
from starlette.testclient import TestClient

from app.cv.archive import archive_scan, archive_scan_safe, classify, to_clean_jpeg
from app.cv.service import PhotoScanResult
from app.main import create_app
from app.ratelimit import reset_rate_limits

PIL = pytest.importorskip("PIL")


@pytest.fixture()
def archive_dir(tmp_path):
    return tmp_path / "scans"


@pytest.fixture()
def archive_client(monkeypatch, archive_dir) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1000")
    monkeypatch.setenv("SCAN_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("RAG_PROVIDER", raising=False)
    reset_rate_limits()
    return TestClient(create_app())


def _photo(client: TestClient, payload: bytes, url: str = "/v1/scan/photo"):
    return client.post(url, files={"image": ("label.jpg", payload, "image/jpeg")})


def _sidecars(root, bucket: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / bucket).rglob("*.json"))]


def _result(**overrides) -> PhotoScanResult:
    base = dict(best_guess_slug="x", slug="x", card=None, top1_score=0.95, gap=0.1,
                ocr_verified=False, not_in_catalog=False, timing_ms=10)
    base.update(overrides)
    return PhotoScanResult(**base)


def test_archive_is_off_by_default(client: TestClient, monkeypatch):
    assert client.app.state.settings.scan_archive_dir is None
    calls: list = []
    monkeypatch.setattr("app.routers.scan.archive_scan_safe", lambda *a, **k: calls.append(a))
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200
    assert calls == [], "без SCAN_ARCHIVE_DIR архив не вызывается вовсе"


def test_confident_scan_goes_to_confident_bucket(archive_client, archive_dir):
    r = _photo(archive_client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200 and r.json()["not_in_catalog"] is False
    [meta] = _sidecars(archive_dir, "confident")
    assert meta["predicted_slug"] == "shato-vymysel-cabernet"
    assert meta["verified_slug"] is None, "истину проставляет человек при сверке"
    assert (archive_dir / "index.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_low_score_scan_goes_to_failed_bucket(archive_client, archive_dir):
    r = _photo(archive_client, b"MOCKPHOTO:weak:shato-vymysel-cabernet")
    assert r.json()["not_in_catalog"] is True
    [meta] = _sidecars(archive_dir, "failed")
    assert meta["reason"] == "floor"
    assert meta["predicted_slug"] == "shato-vymysel-cabernet", "лучшая догадка сохраняется для сверки"


def test_unresolved_near_dup_goes_to_unsure_bucket(archive_client, archive_dir):
    r = _photo(archive_client, b"MOCKPHOTO:near-dup:NONE")
    assert r.json()["not_in_catalog"] is True
    [meta] = _sidecars(archive_dir, "unsure")
    assert meta["reason"] == "margin"


@pytest.mark.parametrize("url", ["/v1/scan/photo?flat=1", "/v1/eval/predict"])
def test_script_modes_are_never_archived(archive_client, archive_dir, url):
    r = _photo(archive_client, b"MOCKPHOTO:shato-vymysel-cabernet", url=url)
    assert r.status_code == 200
    assert not archive_dir.exists(), "приватная выборка кейсодержателя не должна оседать у нас"


def test_undecodable_upload_keeps_only_sidecar(archive_dir):
    archive_scan(str(archive_dir), b"not-an-image", _result(), abs_floor=0.82, index_version="v")
    [meta] = _sidecars(archive_dir, "confident")
    assert meta["image_saved"] is False
    assert not list((archive_dir / "confident").rglob("*.jpg")), "исходные байты не пишутся"


def test_clean_jpeg_drops_exif_gps():
    from PIL import Image

    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (55.0, 45.0, 0.0), 3: "E", 4: (37.0, 37.0, 0.0)}  # GPSInfo
    src = io.BytesIO()
    Image.new("RGB", (64, 48), "red").save(src, "JPEG", exif=exif)
    assert Image.open(io.BytesIO(src.getvalue())).getexif().get(0x8825), "фикстура обязана нести GPS"

    jpeg, info = to_clean_jpeg(src.getvalue())
    assert not Image.open(io.BytesIO(jpeg)).getexif(), "метаданные, включая GPS, вычищены"
    assert (info["width"], info["height"]) == (64, 48)


def test_classify_buckets():
    assert classify(_result(), 0.82) == ("confident", "gate_passed")
    assert classify(_result(ocr_verified=True), 0.82) == ("confident", "ocr_verified")
    assert classify(_result(slug=None, not_in_catalog=True, top1_score=0.9), 0.82) == ("unsure", "margin")
    assert classify(_result(slug=None, not_in_catalog=True, top1_score=0.5), 0.82) == ("failed", "floor")
    assert classify(_result(slug=None, not_in_catalog=True, top1_score=None), 0.82) == ("failed", "no_match")


def test_archive_failure_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    archive_scan_safe(str(blocker / "sub"), b"x", _result(), abs_floor=0.82, index_version=None)
