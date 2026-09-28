"""Optional isolated CPU shelf subprocess, supervised inside the API service cgroup."""
import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


@asynccontextmanager
async def shelf_lifespan(app):
    if os.environ.get("VINCHIK_SHELF_LOCAL") != "1":
        yield
        return
    repo = Path(__file__).resolve().parents[3]
    python = os.environ["SHELF_PYTHON"]
    env = dict(os.environ, PYTHONPATH=str(repo / "apps/shelf-finder/server/src"))
    # Some KVM CPUs misreport the microarchitecture to OpenBLAS (SIGILL in
    # OpenCV RANSAC). Scope the explicitly configured core to this child only.
    core = os.environ.get("SHELF_OPENBLAS_CORETYPE")
    if core:
        env["OPENBLAS_CORETYPE"] = core
    env["OPENBLAS_NUM_THREADS"] = os.environ.get("SHELF_CPU_THREADS", "2")
    env["MALLOC_ARENA_MAX"] = "2"
    process = None

    async def supervise():
        nonlocal process
        while True:
            try:
                process = await asyncio.create_subprocess_exec(
                    python, "-m", "uvicorn", "shelf_api.app:app", "--host", "127.0.0.1",
                    "--port", "8086", "--workers", "1", env=env,
                )
                code = await process.wait()
                log.error("CPU shelf process exited (%s), retrying", code)
            except OSError:
                log.exception("CPU shelf process could not start")
            await asyncio.sleep(5)

    task = asyncio.create_task(supervise())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
