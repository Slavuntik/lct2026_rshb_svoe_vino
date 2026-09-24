"""Тесты отказов VLM-шлюза (задача тимлида 22.09, расширение брифа scan-budget,
п.10) — НАСТОЯЩИЙ локальный HTTP-сервер на случайном порту (не мок urlopen):
проверяет реальное поведение сокета (в частности "медленная выдача" — п.7,
`urllib`/`http.client` ограничивает КАЖДУЮ операцию с сокетом отдельно, не весь
запрос, см. `app/cv/vision_llm.py::_read_response_within_deadline`).

Режимы шлюза: разрыв соединения, 500, 429, битый JSON, пустые поля, зависание
дольше бюджета, медленная (потрюкльная) выдача тела. Для flat (`/v1/eval/predict`)
и rich (`/v1/scan/photo`): всегда 200 и валидный слаг, задержка ≤ бюджет+допуск,
ответ равен ответу с выключенной моделью (честная деградация на CV+OCR); после
`VISION_LLM_BREAKER_FAILS` сбоев подряд следующий скан не ждёт дедлайн модели
вовсе; после паузы и успеха предохранитель закрыт.
"""
from __future__ import annotations

import dataclasses
import io
import json as _json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from starlette.testclient import TestClient

from app.config import Settings
from app.cv import service as service_module
from app.cv import vision_llm
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m


def _jpeg() -> bytes:
    """Настоящий, валидный JPEG (не 3-байтовая заглушка) — `read_label_or_raise()`
    сам декодирует вход (`prepare_image()`, PIL `Image.open().load()`) ДО
    сетевого вызова; битые байты дают "" РАНЬШЕ, чем дойдёт до шлюза (см.
    докстринг), а этим тестам нужно дойти именно до сети."""
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), (120, 30, 40)).save(buf, format="JPEG")
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Фейковый шлюз — настоящий HTTP-сервер, режим переключается атрибутом класса
# --------------------------------------------------------------------------------------


def _ok_payload() -> bytes:
    return _json.dumps({"choices": [{"message": {"content": '{"winery": "ТАБИЯ", "name": "Резерв"}'}}]}).encode()


class _FakeGatewayHandler(BaseHTTPRequestHandler):
    mode = "ok"
    hang_s = 0.0
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 — тише в выводе тестов
        pass

    def do_POST(self):  # noqa: N802 — имя метода диктует http.server
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        mode = type(self).mode

        if mode == "hang":
            time.sleep(type(self).hang_s)
            mode = "ok"

        if mode == "http_500":
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"internal error")
            return
        if mode == "http_429":
            self.send_response(429)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"too many requests")
            return
        if mode == "malformed_json":
            body = b"{not valid json at all"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "empty_fields":
            payload = _json.dumps({"choices": [{"message": {"content": '{"winery": "", "name": ""}'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if mode == "slow_trickle":
            # Тимлид 22.09 (п.7): каждый байт — отдельным write+flush, с паузой
            # МЕНЬШЕ сокет-таймаута КАЖДОГО отдельного recv, но СУММА пауз
            # намного больше настроенного timeout_s — воспроизводит "провисание
            # по операции засчитывается, а весь запрос — нет".
            payload = _ok_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for byte in payload:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.05)
            return

        payload = _ok_payload()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _FakeGateway:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGatewayHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def set_mode(self, mode: str, *, hang_s: float = 0.0) -> None:
        _FakeGatewayHandler.mode = mode
        _FakeGatewayHandler.hang_s = hang_s

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_gateway():
    gw = _FakeGateway()
    yield gw
    gw.stop()


def _closed_port_url() -> str:
    """Порт, на котором ТОЧНО никто не слушает (сокет открыт и сразу закрыт) —
    "разрыв соединения" без реального сервера."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/v1"


# --------------------------------------------------------------------------------------
# vision_llm.read_label_or_raise() напрямую — каждый режим по отдельности
# --------------------------------------------------------------------------------------


def test_connection_refused_raises_vision_llm_error():
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(
            _jpeg(), url=_closed_port_url(), key="k", model="m", timeout_s=1.0,
        )


def test_http_500_raises_vision_llm_error(fake_gateway):
    fake_gateway.set_mode("http_500")
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=1.0)


def test_http_429_raises_vision_llm_error(fake_gateway):
    fake_gateway.set_mode("http_429")
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=1.0)


def test_malformed_json_raises_vision_llm_error(fake_gateway):
    fake_gateway.set_mode("malformed_json")
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=1.0)


def test_empty_fields_is_a_legal_empty_answer_not_an_error(fake_gateway):
    """Пустые, но ВАЛИДНЫЕ поля — НЕ VisionLLMError (модель честно ничего не
    увидела) — отличие от сбоя нужно предохранителю (_ModelBreaker)."""
    fake_gateway.set_mode("empty_fields")
    text = vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=1.0)
    assert text == ""


def test_hang_longer_than_timeout_raises_within_budget(fake_gateway):
    fake_gateway.set_mode("hang", hang_s=2.0)
    t0 = time.monotonic()
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=0.3)
    assert time.monotonic() - t0 < 1.5, "urlopen(timeout=0.3) обязан оборвать зависшее соединение"


def test_slow_trickle_body_is_bounded_by_deadline_not_full_trickle_duration(fake_gateway):
    """(7) Тело отдаётся ~1.4с суммарно (28 байт * 0.05с), но по 1 байту за
    операцию — короче любого разумного сокет-таймаута. `_read_response_within_
    deadline()` обязан оборвать чтение около timeout_s, а не ждать весь трикл."""
    fake_gateway.set_mode("slow_trickle")
    t0 = time.monotonic()
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_or_raise(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=0.3)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"не должны ждать весь трикл (~1.4с), elapsed={elapsed}"


def test_read_label_thin_wrapper_never_raises_on_any_gateway_mode(fake_gateway):
    """read_label() (в отличие от read_label_or_raise()) — старый контракт,
    никогда не бросает, для существующих вызывающих кодов (прогрев и т.п.)."""
    for mode in ("http_500", "http_429", "malformed_json", "empty_fields"):
        fake_gateway.set_mode(mode)
        assert vision_llm.read_label(_jpeg(), url=fake_gateway.url, key="k", model="m", timeout_s=1.0) == ""


# --------------------------------------------------------------------------------------
# Полный путь /v1/scan/photo и /v1/eval/predict через реальный шлюз — всегда
# 200 + валидный слаг, задержка ≤ бюджет+допуск, ответ = ответу с моделью,
# выключенной вовсе (честная деградация на локальный CV+OCR путь)
# --------------------------------------------------------------------------------------

_GATEWAY_CATALOG_ROWS = [
    {"Slug": "cv-only-target", "Название вина": "Простое Вино", "Винодельня": "Простая Винодельня"},
]


def _setup_gateway_scan(app, tmp_path, monkeypatch, *, gateway_url: str | None, choose: str = "merge"):
    idx = _FusionImageIndex([_m("cv-only-target", 0.90)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    overrides: dict[str, object] = dict(
        cv_fusion_text_source="vlm", vision_llm_timeout_s=0.3, cv_scan_budget_s=1.0,
        vision_llm_breaker_fails=3, vision_llm_breaker_cooldown_s=0.3, cv_fusion_choose=choose,
    )
    if gateway_url is not None:
        overrides |= {"vision_llm_url": gateway_url, "vision_llm_key": "fake-key"}
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=_GATEWAY_CATALOG_ROWS, **overrides)


@pytest.mark.parametrize("mode", ["http_500", "http_429", "malformed_json", "hang", "slow_trickle"])
def test_rich_scan_always_200_and_matches_model_disabled_baseline(
    client: TestClient, app, tmp_path, monkeypatch, fake_gateway, mode,
):
    fake_gateway.set_mode(mode, hang_s=2.0)
    _setup_gateway_scan(app, tmp_path, monkeypatch, gateway_url=fake_gateway.url)

    t0 = time.monotonic()
    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert elapsed < 2.0, f"mode={mode} elapsed={elapsed}"
    body = r.json()
    assert body["slug"] == "cv-only-target"

    # baseline — модель вообще не настроена (тот же случай "не ответила ни одна")
    app.state.image_index = _FusionImageIndex([_m("cv-only-target", 0.90)])
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _setup_gateway_scan(app, tmp_path, monkeypatch, gateway_url=None)
    baseline = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert baseline.status_code == 200
    assert baseline.json()["slug"] == body["slug"]


@pytest.mark.parametrize("mode", ["http_500", "hang"])
def test_flat_eval_predict_always_200_with_valid_slug(client: TestClient, app, tmp_path, monkeypatch, fake_gateway, mode):
    fake_gateway.set_mode(mode, hang_s=2.0)
    _setup_gateway_scan(app, tmp_path, monkeypatch, gateway_url=fake_gateway.url)

    t0 = time.monotonic()
    r = client.post("/v1/eval/predict", files={"image": ("label.jpg", b"MOCKPHOTO:whatever", "image/jpeg")})
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert elapsed < 2.0
    assert r.json()["slug"] == "cv-only-target"


def test_breaker_opens_after_three_failures_fourth_scan_does_not_wait(
    client: TestClient, app, tmp_path, monkeypatch, fake_gateway,
):
    fake_gateway.set_mode("hang", hang_s=2.0)
    _setup_gateway_scan(app, tmp_path, monkeypatch, gateway_url=fake_gateway.url)

    timings = []
    for _ in range(4):
        t0 = time.monotonic()
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
        timings.append(time.monotonic() - t0)
        assert r.status_code == 200

    assert all(t < 2.0 for t in timings[:3]), timings  # первые 3 ждут ~timeout_s (0.3с) каждый
    assert timings[3] < 0.5, (
        f"4-й скан не должен ждать дедлайн модели вовсе — предохранитель открыт, timings={timings}"
    )


def test_breaker_recovers_after_cooldown_and_one_success(
    client: TestClient, app, tmp_path, monkeypatch, fake_gateway,
):
    """После паузы (VISION_LLM_BREAKER_COOLDOWN_S) и УСПЕШНОГО ответа шлюза
    предохранитель закрыт — следующий скан снова пробует модель."""
    fake_gateway.set_mode("hang", hang_s=2.0)
    _setup_gateway_scan(app, tmp_path, monkeypatch, gateway_url=fake_gateway.url)

    for _ in range(3):
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
        assert r.status_code == 200

    time.sleep(0.35)  # cooldown_s=0.3 в _setup_gateway_scan
    fake_gateway.set_mode("ok")
    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200
    assert service_module._MODEL_BREAKERS["vlm"].allow(cooldown_s=60) is True, "успешный пробный скан обязан закрыть предохранитель"
