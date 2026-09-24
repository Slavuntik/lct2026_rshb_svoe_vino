import threading
import asyncio
import pytest
import httpx
from shelf_api.app import create_app
from test_app import FakeEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_health_served_during_background_warmup():
    release = threading.Event()

    def load():
        release.wait(5)
        return FakeEngine()

    app = create_app(load)
    async with app.router.lifespan_context(app):
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/v1/shelf/health")
                assert (
                    response.status_code == 503
                    and response.json()["state"] == "warming"
                )
                release.set()
                for _ in range(100):
                    response = await client.get("/v1/shelf/health")
                    if response.status_code == 200:
                        break
                    await asyncio.sleep(0.01)
                assert response.json()["ready"] is True
        finally:
            release.set()
