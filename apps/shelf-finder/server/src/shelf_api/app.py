"""An isolated HTTP service. Models load in the background; readiness is explicit."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from io import BytesIO
import logging
import os
from pathlib import Path
import uuid
import warnings

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile
from .schema import ScanResponse, HealthResponse

log = logging.getLogger("shelf_api")


@dataclass
class Settings:
    models: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "SHELF_MODELS_DIR",
                str(Path(__file__).resolve().parents[3] / "artifacts/server-models"),
            )
        ).resolve()
    )
    device: str = field(default_factory=lambda: os.getenv("SHELF_DEVICE", "auto"))
    threads: int = field(
        default_factory=lambda: int(os.getenv("SHELF_CPU_THREADS", "4"))
    )
    static: str = field(default_factory=lambda: os.getenv("SHELF_STATIC_DIR", ""))
    cors: list[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv("SHELF_CORS_ORIGINS", "").split(",")
            if x.strip()
        ]
    )
    profile: str = field(default_factory=lambda: os.getenv("SHELF_PROFILE", "baseline"))
    accuracy_models: str = field(
        default_factory=lambda: os.getenv("SHELF_ACCURACY_MODELS", "")
    )
    semantic_models: str = field(
        default_factory=lambda: os.getenv("SHELF_SEMANTIC_MODELS", "")
    )
    semantic_scope: str = field(
        default_factory=lambda: os.getenv("SHELF_SEMANTIC_SCOPE", "selected")
    )
    rescue_limit: int = field(
        default_factory=lambda: int(os.getenv("SHELF_RESCUE_LIMIT", "8"))
    )
    budget_seconds: float = field(
        default_factory=lambda: float(os.getenv("SHELF_BUDGET_SECONDS", "10"))
    )
    ocr: bool = field(default_factory=lambda: os.getenv("SHELF_OCR", "0") == "1")
    vlm_url: str = field(
        default_factory=lambda: os.getenv(
            "SHELF_LITELLM_URL", os.getenv("VISION_LLM_URL", "")
        )
    )
    vlm_key: str = field(
        default_factory=lambda: os.getenv(
            "SHELF_LITELLM_API_KEY", os.getenv("VISION_LLM_KEY", "")
        ),
        repr=False,
    )
    vlm_model: str = field(
        default_factory=lambda: os.getenv(
            "SHELF_LITELLM_MODEL", "qwen3.8-27b-uncensored"
        )
    )
    vlm_timeout: float = field(
        default_factory=lambda: float(os.getenv("SHELF_LITELLM_TIMEOUT", "60"))
    )
    vlm_limit: int = field(
        default_factory=lambda: int(os.getenv("SHELF_LITELLM_LIMIT", "3"))
    )
    vlm_references: str = field(
        default_factory=lambda: os.getenv("SHELF_LITELLM_REFERENCES_DIR", "")
    )
    scan_timeout: int = field(
        default_factory=lambda: int(os.getenv("SHELF_SCAN_TIMEOUT_SECONDS", "300"))
    )
    max_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 24_000_000
    upload_seconds: float = 30


def decode_image(data, settings):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("JPEG", "PNG"):
                    raise HTTPException(415, "Поддерживаются JPEG и PNG")
                if image.width * image.height > settings.max_pixels:
                    raise HTTPException(413, "Слишком большое разрешение фото")
                if getattr(image, "n_frames", 1) != 1:
                    raise HTTPException(415, "Нужен один неподвижный снимок")
                return ImageOps.exif_transpose(image).convert("RGB")
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "Слишком большое разрешение фото")
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(400, "Не удалось прочитать изображение")


def create_app(factory=None, settings=None):
    settings = settings or Settings()
    if not 15 <= settings.scan_timeout <= 600:
        raise ValueError("SHELF_SCAN_TIMEOUT_SECONDS must be 15..600")
    state = {"engine": None, "busy": False, "failed": False, "inference": None}
    if factory is None:

        def factory():
            from .engine import ShelfEngine

            if settings.profile == "litellm":
                from .vlm import LiteLLMClient, LiteLLMEngine

                client = LiteLLMClient(
                    settings.vlm_url,
                    settings.vlm_key,
                    settings.vlm_model,
                    settings.vlm_timeout,
                    settings.vlm_references,
                )
                return LiteLLMEngine(
                    settings.models,
                    settings.device,
                    settings.threads,
                    client,
                    settings.vlm_limit,
                )
            if settings.profile == "accuracy":
                if not settings.accuracy_models:
                    raise ValueError(
                        "SHELF_ACCURACY_MODELS is required for accuracy profile"
                    )
                from .accuracy import AccuracyEngine

                return AccuracyEngine(
                    settings.models,
                    Path(settings.accuracy_models),
                    settings.device,
                    settings.threads,
                    rescue_limit=settings.rescue_limit,
                    budget_seconds=settings.budget_seconds,
                    semantic_scope=settings.semantic_scope,
                    use_ocr=settings.ocr,
                    semantic_dir=(
                        Path(settings.semantic_models)
                        if settings.semantic_models
                        else None
                    ),
                )
            if settings.profile != "baseline":
                raise ValueError("Unknown shelf profile")
            return ShelfEngine(settings.models, settings.device, settings.threads)

    async def initialize():
        try:
            state["engine"] = await asyncio.to_thread(factory)
        except Exception:
            state["failed"] = True
            log.exception("Shelf model initialization failed")

    @asynccontextmanager
    async def lifespan(app):
        loading = asyncio.create_task(initialize())
        yield
        await loading
        if state["inference"] is not None:
            await state["inference"]

    app = FastAPI(title="Shelf Finder API", version="0.1.0", lifespan=lifespan)
    app.state.runtime = state
    if settings.cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            expose_headers=["Retry-After"],
        )

    def engine():
        if state["engine"] is None:
            raise HTTPException(
                503,
                "Модуль не готов" if state["failed"] else "Модели прогреваются",
                headers={"Retry-After": "5"},
            )
        return state["engine"]

    @app.get("/v1/shelf/health", response_model=HealthResponse)
    async def health():
        current = state["engine"]
        ready = current is not None
        return JSONResponse(
            {
                "ready": ready,
                "busy": state["busy"],
                "state": (
                    "ready" if ready else "failed" if state["failed"] else "warming"
                ),
                "catalogSize": len(current.wines) if ready else 0,
                "device": getattr(current, "device", settings.device),
                "profile": settings.profile,
                "scanTimeoutSeconds": settings.scan_timeout,
            },
            status_code=200 if ready else 503,
            headers={} if ready else {"Retry-After": "5"},
        )

    @app.get("/v1/shelf/catalog")
    async def catalog():
        current = engine()
        return {
            "catalogVersion": current.catalog_version,
            "wines": [
                {k: w.get(k, "") for k in ("id", "name", "brand", "region", "group")}
                for w in current.wines.values()
            ],
        }

    @app.post(
        "/v1/shelf/scan",
        response_model=ScanResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["image"],
                            "properties": {
                                "image": {"type": "string", "format": "binary"}
                            },
                        }
                    }
                },
            }
        },
    )
    async def scan(request: Request):
        current = engine()
        if state["busy"]:
            raise HTTPException(
                503,
                "Сервер занят, повторите через несколько секунд",
                headers={"Retry-After": "3"},
            )
        state["busy"] = True
        image = None
        form = None
        try:
            if (
                not request.headers.get("content-type", "")
                .lower()
                .startswith("multipart/form-data;")
            ):
                raise HTTPException(415, "Отправьте multipart/form-data с полем image")
            # Bound even chunked multipart uploads before invoking the multipart parser.
            body = bytearray()
            try:
                async with asyncio.timeout(settings.upload_seconds):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > settings.max_bytes + 65536:
                            raise HTTPException(413, "Фото превышает 20 МБ")
            except TimeoutError:
                raise HTTPException(408, "Загрузка фото заняла слишком много времени")
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            bounded = Request(request.scope, receive)
            form = await bounded.form(max_files=1, max_fields=0)
            upload = form.get("image")
            if not isinstance(upload, UploadFile):
                raise HTTPException(400, "Нужно поле image с файлом")
            data = await upload.read(settings.max_bytes + 1)
            if len(data) > settings.max_bytes:
                raise HTTPException(413, "Фото превышает 20 МБ")
            image = await asyncio.to_thread(decode_image, data, settings)
            width, height = image.size
            task = asyncio.create_task(asyncio.to_thread(current.scan, image))
            state["inference"] = task
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                # Client cancellation does not free the GPU admission slot before native work stops.
                await task
                raise
            except Exception:
                log.exception("Shelf inference failed")
                raise HTTPException(500, "Не удалось обработать витрину")
            return {
                **result,
                "requestId": str(uuid.uuid4()),
                "image": {"width": width, "height": height},
            }
        finally:
            if form is not None:
                await form.close()
            if image is not None:
                image.close()
            state["inference"] = None
            state["busy"] = False

    if settings.static:
        root = Path(settings.static).resolve()

        @app.middleware("http")
        async def isolation(request, call_next):
            response = await call_next(request)
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
            return response

        app.mount("/", StaticFiles(directory=root, html=True), name="frontend")
    return app


app = create_app()
