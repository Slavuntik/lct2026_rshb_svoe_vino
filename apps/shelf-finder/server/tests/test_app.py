import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
import threading

import httpx
from PIL import Image
import pytest

from shelf_api.app import Settings, create_app


@pytest.fixture
def anyio_backend():
    return "asyncio"


def photo(size=(40, 60), exif=None):
    out = BytesIO()
    Image.new("RGB", size, "white").save(
        out, "JPEG", **({"exif": exif} if exif else {})
    )
    return out.getvalue()


class FakeEngine:
    catalog_version = "test"
    wines = {
        "wine": {"id": "wine", "name": "Тест", "brand": "", "region": "", "group": ""}
    }

    def scan(self, image):
        return {
            "pipelineVersion": "fake",
            "catalogVersion": "test",
            "detectedCount": 1,
            "matches": [
                {"box": [0.1, 0.2, 0.7, 0.8], "wineId": "wine", "name": "Тест"}
            ],
            "timingsMs": {"processing": 1},
            "warnings": [],
        }


@asynccontextmanager
async def client_for(factory=FakeEngine, settings=None):
    app = create_app(factory, settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for _ in range(100):
                r = await client.get("/v1/shelf/health")
                if r.status_code == 200 or r.json()["state"] == "failed":
                    break
                await asyncio.sleep(0.01)
            yield client, app


@pytest.mark.anyio
async def test_catalog_and_normalized_image_response():
    exif = Image.Exif()
    exif[274] = 6
    async with client_for() as (client, _):
        catalog = (await client.get("/v1/shelf/catalog")).json()
        assert "references" not in catalog["wines"][0]
        result = await client.post(
            "/v1/shelf/scan",
            files={"image": ("photo.jpg", photo(exif=exif), "image/jpeg")},
        )
        assert result.status_code == 200
        assert result.json()["image"] == {"width": 60, "height": 40}
        assert result.json()["matches"][0]["box"] == [0.1, 0.2, 0.7, 0.8]


@pytest.mark.anyio
async def test_invalid_and_oversized_inputs_release_slot():
    settings = Settings(max_bytes=2000, max_pixels=3000)
    async with client_for(settings=settings) as (client, app):
        for data, expected in [
            (b"broken", 400),
            (photo((70, 70)), 413),
            (b"x" * 70000, 413),
        ]:
            response = await client.post(
                "/v1/shelf/scan", files={"image": ("x.jpg", data, "image/jpeg")}
            )
            assert response.status_code == expected
            assert not app.state.runtime["busy"]
        response = await client.post(
            "/v1/shelf/scan", files={"wrong": ("x.jpg", photo(), "image/jpeg")}
        )
        assert response.status_code == 400
        assert (
            await client.post(
                "/v1/shelf/scan", files={"image": ("x.jpg", photo(), "image/jpeg")}
            )
        ).status_code == 200


@pytest.mark.anyio
async def test_startup_failure_is_not_ready():
    def failed():
        raise RuntimeError("missing models")

    async with client_for(failed) as (client, _):
        r = await client.get("/v1/shelf/health")
        assert r.status_code == 503 and r.json()["state"] == "failed"
        assert (
            await client.post("/v1/shelf/scan", files={"image": ("x.jpg", photo())})
        ).status_code == 503


@pytest.mark.anyio
async def test_busy_and_cancelled_request_hold_slot_until_native_work_finishes():
    entered = threading.Event()
    release = threading.Event()

    class Slow(FakeEngine):
        def scan(self, image):
            entered.set()
            release.wait(5)
            return super().scan(image)

    async with client_for(Slow) as (client, app):
        task = asyncio.create_task(
            client.post("/v1/shelf/scan", files={"image": ("x.jpg", photo())})
        )
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            task.cancel()
            await asyncio.sleep(0.02)
            assert app.state.runtime["busy"]
            r = await client.post("/v1/shelf/scan", files={"image": ("x.jpg", photo())})
            assert r.status_code == 503 and r.headers["retry-after"] == "3"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not app.state.runtime["busy"]


@pytest.mark.anyio
async def test_chunked_upload_is_bounded_without_content_length():
    async def stream():
        for _ in range(8):
            yield b"x" * 10000

    async with client_for(settings=Settings(max_bytes=1000)) as (client, app):
        r = await client.post(
            "/v1/shelf/scan",
            content=stream(),
            headers={"content-type": "multipart/form-data; boundary=test"},
        )
        assert r.status_code == 413
        assert not app.state.runtime["busy"]
