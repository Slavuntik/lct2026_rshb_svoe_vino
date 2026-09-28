"""Authenticated gateway to the independent CPU/GPU shelf service.

Short polling avoids requiring a longer timeout on the public reverse proxy.
Jobs are bounded, private to their owner, and ephemeral across API restarts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from ..security import Principal, get_current_principal
from ..ratelimit import rate_limit

router = APIRouter(prefix="/shelf", tags=["shelf"])
MAX_BYTES = 20 * 1024 * 1024
RESULT_TTL = 120
MAX_JOBS = 16
SCAN_TIMEOUT = 300


def upstream() -> str:
    url = os.environ.get("VINCHIK_SHELF_URL", "").rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, "Shelf service is not configured")
    return url


def remote_json(path: str, image: bytes | None = None) -> tuple[int, dict]:
    url = upstream() + path
    try:
        with httpx.Client(timeout=httpx.Timeout(SCAN_TIMEOUT, connect=5), follow_redirects=False, trust_env=False) as client:
            kwargs = {"files": {"image": ("shelf.jpg", image, "image/jpeg")}} if image is not None else {"timeout": 5}
            with client.stream("POST" if image is not None else "GET", url, **kwargs) as response:
                chunks = bytearray()
                deadline = time.monotonic() + (SCAN_TIMEOUT if image is not None else 5)
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2 * 1024 * 1024 or time.monotonic() > deadline:
                        raise ValueError("Invalid upstream response")
                import json
                data = json.loads(chunks)
                if not isinstance(data, dict) or response.is_redirect:
                    raise ValueError("Invalid upstream response")
                if response.status_code >= 400:
                    # Never expose upstream addresses, tracebacks or credentials.
                    return response.status_code, {"detail": "Shelf service is busy or unavailable"}
                return response.status_code, data
    except (httpx.HTTPError, ValueError):
        return 503, {"detail": "Shelf service is unavailable"}


@dataclass
class Job:
    owner: str
    state: str = "running"
    status: int = 202
    result: dict = field(default_factory=dict)
    finished: float | None = None


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}

    def prune(self):
        now = time.monotonic()
        self.jobs = {key: job for key, job in self.jobs.items()
                     if job.finished is None or now - job.finished < RESULT_TTL}

    def start(self, owner: str, image: bytes) -> str:
        with self.lock:
            self.prune()
            if len(self.jobs) >= MAX_JOBS or any(j.state == "running" for j in self.jobs.values()):
                raise HTTPException(503, "Shelf service is busy", headers={"Retry-After": "3"})
            key = uuid.uuid4().hex
            self.jobs[key] = Job(owner)
        threading.Thread(target=self.run, args=(key, image), daemon=True, name="shelf-job").start()
        return key

    def run(self, key: str, image: bytes):
        try:
            status, result = remote_json("/v1/shelf/scan", image)
        except Exception:
            status, result = 503, {"detail": "Shelf service is unavailable"}
        with self.lock:
            job = self.jobs[key]
            job.status, job.result = status, result
            job.state = "done" if status == 200 else "failed"
            job.finished = time.monotonic()

    def read(self, key: str, owner: str) -> dict:
        with self.lock:
            self.prune()
            job = self.jobs.get(key)
            if job is None or job.owner != owner:
                raise HTTPException(404, "Shelf job not found or expired")
            return {"state": job.state, "status": job.status, "result": job.result}


def jobs(request: Request) -> Jobs:
    return request.app.state.shelf_jobs


@router.get("/health")
async def health():
    try:
        status, data = await asyncio.to_thread(remote_json, "/v1/shelf/health")
    except HTTPException:
        status, data = 503, {}
    ready = status == 200 and data.get("ready") is True
    public = {key: data[key] for key in ("busy", "state", "catalogSize", "device", "profile") if key in data}
    return JSONResponse({**public, "ready": ready, "asyncJobs": True,
                         "scanTimeoutSeconds": SCAN_TIMEOUT + 15},
                        status_code=200 if ready else 503)


async def read_image(image: UploadFile) -> bytes:
    try:
        content = await image.read(MAX_BYTES + 1)
    finally:
        await image.close()
    if not content or len(content) > MAX_BYTES:
        raise HTTPException(413 if content else 422, "Image must be between 1 byte and 20 MiB")
    return content


@router.post("/jobs", status_code=202, dependencies=[Depends(rate_limit("shelf"))])
async def start_job(request: Request, image: UploadFile = File(...),
                    principal: Principal = Depends(get_current_principal)):
    upstream()
    key = jobs(request).start(principal.id, await read_image(image))
    return {"jobId": key, "state": "running"}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    return jobs(request).read(job_id, principal.id)


@router.post("/scan", dependencies=[Depends(rate_limit("shelf"))])
async def scan(request: Request, image: UploadFile = File(...),
               principal: Principal = Depends(get_current_principal)):
    upstream()
    key = jobs(request).start(principal.id, await read_image(image))
    while True:
        result = jobs(request).read(key, principal.id)
        if result["state"] != "running":
            return JSONResponse(result["result"], status_code=result["status"])
        await asyncio.sleep(0.2)
