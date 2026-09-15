"""HTTP API сканера.

Запуск: ``uvicorn winescan.service.app:app --host 0.0.0.0 --port 8080``
(порт 8080 и путь /v1/eval/predict ожидает participant_test.sh кейсодержателя).
"""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from winescan.config import get_paths


class ScannerLike(Protocol):
    cards: dict[str, dict]

    def scan(self, image: Image.Image): ...

    def top1_slug(self, image: Image.Image) -> str: ...


def _read_image(upload: UploadFile) -> Image.Image:
    data = upload.file.read()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"не удалось прочитать изображение: {exc}") from exc


def create_app(scanner_factory: Callable[[], ScannerLike]) -> FastAPI:
    state: dict[str, ScannerLike] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        scanner = scanner_factory()
        if hasattr(scanner, "warmup"):
            scanner.warmup()  # первая инференс-итерация на CUDA медленная; делаем её до первого запроса
        state["scanner"] = scanner
        yield
        state.clear()

    app = FastAPI(title="WineScan", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "wines": len(state["scanner"].cards)}

    @app.post("/v1/eval/predict")
    def eval_predict(image: UploadFile = File(...)) -> dict:
        """Контракт скрипта оценки: плоский {"slug": "..."}, всегда лучший кандидат."""
        return {"slug": state["scanner"].top1_slug(_read_image(image))}

    @app.post("/v1/scan")
    def scan(image: UploadFile = File(...)) -> dict:
        return state["scanner"].scan(_read_image(image)).to_dict()

    @app.get("/v1/wines/{slug}")
    def wine(slug: str) -> dict:
        card = state["scanner"].cards.get(slug)
        if card is None:
            raise HTTPException(status_code=404, detail="вино не найдено")
        return card

    @app.get("/v1/wines/{slug}/image")
    def wine_image(slug: str) -> FileResponse:
        card = state["scanner"].cards.get(slug)
        if card is None or not card["image"]["file"]:
            raise HTTPException(status_code=404, detail="изображение не найдено")
        return FileResponse(get_paths().uploads_dir / card["image"]["file"])

    return app


def _default_scanner() -> ScannerLike:
    from winescan.service.pipeline import Scanner, ScannerConfig

    return Scanner(ScannerConfig.from_env())


app = create_app(_default_scanner)
