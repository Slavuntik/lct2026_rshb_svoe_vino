import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from winescan.service.app import create_app
from winescan.service.pipeline import ScanResult

CARD = {"slug": "kokur-suhoe-2025", "name": "Кокур Сухое, 2025", "winery": "Мельниковы", "category": "Белое",
        "region": "Крым", "grapes": ["Кокур"], "description": "свежий цитрусовый аромат",
        "attributes": {"sweetness": "dry", "sparkling": False}, "image": {"file": ""}}  # fmt: skip
ANALOG = {**CARD, "slug": "kokur-winepark", "name": "Кокур", "winery": "WINEPARK"}
RED = {**CARD, "slug": "saperavi", "name": "Саперави", "winery": "Фанагория", "category": "Красное",
       "grapes": ["Саперави"], "description": "выдержка в дубовых бочках"}  # fmt: skip


class FakeScanner:
    cards = {c["slug"]: c for c in (CARD, ANALOG, RED)}
    last_box = None

    def scan(self, image, relative_box=None):
        FakeScanner.last_box = relative_box
        return ScanResult(
            status="found",
            slug=CARD["slug"],
            card=CARD,
            top5=[{"slug": CARD["slug"], "score": 0.9, "name": CARD["name"]}],
            confidence={"score_top1": 0.9, "margin_top1_top2": 0.2, "margin_threshold": None},
            box=None,
        )

    def top1_slug(self, image):
        return CARD["slug"]


@pytest.fixture
def client():
    with TestClient(create_app(FakeScanner)) as test_client:
        yield test_client


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 96), (120, 30, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_eval_predict_returns_flat_slug(client):
    response = client.post("/v1/eval/predict", files={"image": ("q.jpg", _jpeg(), "image/jpeg")})

    assert response.status_code == 200
    assert response.json() == {"slug": "kokur-suhoe-2025"}


def test_scan_returns_card_and_confidence(client):
    body = client.post("/v1/scan", files={"image": ("q.jpg", _jpeg(), "image/jpeg")}).json()

    assert body["status"] == "found" and body["card"]["name"] == "Кокур Сухое, 2025"
    assert body["confidence"]["margin_top1_top2"] == 0.2


def test_scan_accepts_user_box_in_fractions(client):
    files = {"image": ("q.jpg", _jpeg(), "image/jpeg")}

    assert client.post("/v1/scan", files=files).status_code == 200
    assert FakeScanner.last_box is None  # без рамки бутылку ищет детектор

    assert client.post("/v1/scan", files=files, data={"box": "0.2,0.1,0.8,0.95"}).status_code == 200
    assert FakeScanner.last_box == (0.2, 0.1, 0.8, 0.95)

    assert client.post("/v1/scan", files=files, data={"box": "0.2,0.1,0.8"}).status_code == 400
    assert client.post("/v1/scan", files=files, data={"box": "0.5,0.5,0.505,0.9"}).status_code == 400
    assert client.post("/v1/scan", files=files, data={"box": "левее,0.1,0.8,0.9"}).status_code == 400


def test_broken_image_is_400(client):
    response = client.post("/v1/eval/predict", files={"image": ("q.jpg", b"not an image", "image/jpeg")})

    assert response.status_code == 400


def test_wine_card_and_404(client):
    assert client.get("/v1/wines/kokur-suhoe-2025").json()["slug"] == "kokur-suhoe-2025"
    assert client.get("/v1/wines/unknown").status_code == 404
    assert client.get("/health").json() == {"status": "ok", "wines": 3}


def test_analogs_endpoint(client):
    body = client.get("/v1/wines/kokur-suhoe-2025/analogs").json()

    assert [a["slug"] for a in body["analogs"]] == ["kokur-winepark"]
    assert "сорт: Кокур" in body["analogs"][0]["reasons"]
    assert client.get("/v1/wines/unknown/analogs").status_code == 404


def test_scan_reports_top1_and_top5_reliability(monkeypatch, tmp_path):
    """ТЗ: вместе с карточкой сервис показывает уверенность для топ-1 и топ-5."""
    monkeypatch.setenv("WINESCAN_ARTIFACTS_DIR", str(tmp_path))
    (tmp_path / "eval").mkdir(parents=True)
    with TestClient(create_app(FakeScanner)) as test_client:
        files = {"image": ("q.jpg", _jpeg(), "image/jpeg")}
        assert "quality" not in test_client.post("/v1/scan", files=files).json()  # сводки ещё нет

        (tmp_path / "eval" / "summary.json").write_text(json.dumps({
            "generated_at": "2026-09-16T11:15:56",
            "runs": [
                {"run": "старый", "split": "synth_v2", "kind": "сервис", "queries": 100, "top1": 0.5,
                 "top5": 0.6, "finished_at": "2026-09-15T10:00:00"},
                {"run": "свежий", "split": "synth_v2", "kind": "сервис", "queries": 1052, "top1": 0.7139,
                 "top5": 0.7994, "finished_at": "2026-09-16T00:42:11"},
                {"run": "поиск", "split": "synth_v2", "kind": "поиск", "queries": 2103, "top1": 0.9},
            ],
        }), encoding="utf-8")  # fmt: skip

        quality = test_client.post("/v1/scan", files=files).json()["quality"]

    assert quality["splits"]["synth_v2"]["run"] == "свежий"  # берётся самый свежий сквозной прогон
    assert quality["splits"]["synth_v2"]["top1"] == 0.7139 and quality["splits"]["synth_v2"]["top5"] == 0.7994


def test_metrics_endpoint_reads_prepared_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("WINESCAN_ARTIFACTS_DIR", str(tmp_path))
    with TestClient(create_app(FakeScanner)) as test_client:
        assert test_client.get("/v1/metrics").status_code == 404  # сводка ещё не собрана

        (tmp_path / "eval").mkdir(parents=True)
        (tmp_path / "eval" / "summary.json").write_text(
            '{"generated_at": "2026-09-16T08:00:00", "runs": [{"run": "scanner__public__default", "top1": 1.0}]}',
            encoding="utf-8",
        )

        body = test_client.get("/v1/metrics").json()
        assert body["runs"][0]["run"] == "scanner__public__default"


def test_sommelier_endpoints(client):
    questions = client.get("/v1/sommelier/questions").json()["questions"]
    assert questions[0]["id"] == "dish"

    body = client.post("/v1/sommelier/suggest", json={"dish": "meat", "exclude_slugs": []}).json()
    assert body["suggestions"][0]["slug"] == "saperavi" and "18+" in body["disclaimer"]
    assert client.post("/v1/sommelier/suggest", json={"dish": "unknown"}).status_code == 400
