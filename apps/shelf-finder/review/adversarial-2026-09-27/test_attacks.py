"""Regression tests: PASS means the reviewed failure is prevented.
No model downloads, GPUs, external API calls, or production config changes.
"""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "apps/shelf-finder/server/src"))
from shelf_api.app import Settings, create_app
pytest.importorskip("torch")
from shelf_api.vlm import LiteLLMClient, LiteLLMEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


def photo():
    out = BytesIO()
    Image.new("RGB", (80, 120), "white").save(out, "JPEG")
    return out.getvalue()


class FakeEngine:
    wines = {"wine": {"id": "wine", "name": "Synthetic"}}
    catalog_version = "test"

    def scan(self, image):
        return dict(
            pipelineVersion="test",
            catalogVersion="test",
            detectedCount=1,
            matches=[dict(box=[0.1, 0.1, 0.9, 0.9], wineId="wine", name="Synthetic")],
            timingsMs={"processing": 1},
            warnings=[],
        )


@asynccontextmanager
async def client_for(factory=FakeEngine):
    app = create_app(factory, Settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://isolated",
        ) as client:
            for _ in range(100):
                if (await client.get("/v1/shelf/health")).status_code == 200:
                    break
                await asyncio.sleep(0.01)
            yield client, app


def refinement(client):
    engine = object.__new__(LiteLLMEngine)
    engine.vlm_client = client
    engine.vlm_limit = 3
    engine.wines = FakeEngine.wines
    engine.scan_warnings = []
    obs = [
        dict(id="wine", evidence=[], box=[0, 0, 1, 1]),
        dict(
            id=None,
            evidence=[],
            box=[0, 0, 1, 1],
            retrieval_q=object(),
            q=object(),
            ranking=["wine"],
        ),
    ]
    return engine, obs


@pytest.mark.anyio
@pytest.mark.parametrize("attack", ["malformed_choice", "corrupt_reference"])
async def test_optional_vlm_failure_preserves_native_results(
    monkeypatch, tmp_path, attack
):
    client = LiteLLMClient(
        "http://isolated.invalid/v1",
        "fake-key",
        "fake-model",
        references=tmp_path if attack == "corrupt_reference" else None,
    )

    async def malformed_response(self, body, budget):
        return {"choices": [None]}

    monkeypatch.setattr(LiteLLMClient, "request", malformed_response)
    engine, obs = refinement(client)

    class Refiner(FakeEngine):
        def scan(self, image):
            engine.refine(image, obs, ["wine"], 0)
            return super().scan(image)

    async with client_for(Refiner) as (http, app):
        result = await http.post("/v1/shelf/scan", files={"image": ("x.jpg", photo())})
        assert result.status_code == 200
        assert result.json()["matches"][0]["wineId"] == "wine"
        assert engine.last_vlm["failed"]
        assert not app.state.runtime["busy"]
        print(f"ATTACK {attack}: HTTP {result.status_code}; native match preserved")


@pytest.mark.anyio
async def test_malformed_multipart_returns_client_error():
    async with client_for() as (http, app):
        result = await http.post(
            "/v1/shelf/scan",
            content=b"garbage",
            headers={"content-type": "multipart/form-data; boundary=x"},
        )
        assert result.status_code == 400
        assert not app.state.runtime["busy"]
        print(
            f"ATTACK malformed multipart: HTTP {result.status_code}, body={result.text!r}"
        )


@pytest.mark.anyio
async def test_second_cancellation_retains_slot_until_native_thread_finishes():
    entered, released = threading.Event(), threading.Event()
    active = 0
    maximum = 0
    lock = threading.Lock()

    class Slow(FakeEngine):
        def scan(self, image):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                entered.set()
            released.wait(3)
            with lock:
                active -= 1
            return super().scan(image)

    async with client_for(Slow) as (http, app):
        first = asyncio.create_task(
            http.post("/v1/shelf/scan", files={"image": ("x.jpg", photo())})
        )
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            first.cancel()
            await asyncio.sleep(0.02)
            assert app.state.runtime["busy"]
            first.cancel()
            await asyncio.sleep(0.02)
            assert active == 1 and app.state.runtime["busy"]
            second = await http.post(
                "/v1/shelf/scan", files={"image": ("x.jpg", photo())}
            )
            assert second.status_code == 503
            assert maximum == 1
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert active == 0 and not app.state.runtime["busy"]
            retry = await http.post(
                "/v1/shelf/scan", files={"image": ("x.jpg", photo())}
            )
            assert retry.status_code == 200
            print(
                "REGRESSION repeated cancellation: concurrency=1; retry succeeds after release"
            )
        finally:
            released.set()
            if not first.done():
                first.cancel()


def test_litellm_deadline_stops_drip_fed_response():
    payload = b'{"choices":[{"message":{"content":"{\\"bottles\\":[]}"},"finish_reason":"stop"}]}'

    class SlowStream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for i in range(0, len(payload), 10):
                try:
                    self.wfile.write(payload[i : i + 10])
                    self.wfile.flush()
                    time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError):
                    break

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowStream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = LiteLLMClient(
            f"http://127.0.0.1:{server.server_port}/v1",
            "fake-key",
            "fake-model",
            timeout=1,
        )
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            client.choose(
                Image.new("RGB", (80, 120)),
                [{"box": [0, 0, 1, 1], "candidates": ["wine"]}],
                FakeEngine.wines,
            )
        elapsed = time.monotonic() - started
        assert 0.9 <= elapsed < 2.0
        print(
            f"ATTACK drip-fed LiteLLM: configured timeout=1s, completed after {elapsed:.3f}s with total deadline"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_empty_llm_result_reports_requests_and_zero_proposals():
    class Empty:
        def choose(self, *args):
            return {}

    engine, obs = refinement(Empty())
    engine.refine(Image.new("RGB", (80, 120)), obs, ["wine"], 0)
    assert engine.last_vlm == {"requested": 1, "proposed": 0, "confirmed": 0}
    assert "Отправлено Qwen: 1" in engine.scan_warnings[0]
    assert "Предложений: 0" in engine.scan_warnings[0]
    print("ATTACK empty bottles=[]: UI distinguishes requests from zero proposals")
