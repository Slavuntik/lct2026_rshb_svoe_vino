"""HTTP API сканера.

Запуск: ``uvicorn winescan.service.app:app --host 0.0.0.0 --port 8080``
(порт 8080 и путь /v1/eval/predict ожидает participant_test.sh кейсодержателя).
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from winescan.config import get_paths


class ScannerLike(Protocol):
    cards: dict[str, dict]

    def scan(self, image: Image.Image): ...

    def top1_slug(self, image: Image.Image) -> str: ...


DECODE_MIN_SIDE = 1024


def _read_image(upload: UploadFile) -> Image.Image:
    data = upload.file.read()
    try:
        image = Image.open(io.BytesIO(data))
        # JPEG 3024×4032 декодируется сразу с уменьшением (не меньше 1024 по каждой стороне):
        # пайплайн всё равно уменьшает кадр до 1600, а полное декодирование — сотни мс
        image.draft("RGB", (DECODE_MIN_SIDE, DECODE_MIN_SIDE))
        image.load()
        return image
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"не удалось прочитать изображение: {exc}") from exc


def _parse_box(box: str | None) -> tuple[float, float, float, float] | None:
    """«x1,y1,x2,y2» в долях кадра -> рамка; None, если не задана. Ошибка формата — 400."""
    if box is None or not box.strip():
        return None
    try:
        values = tuple(float(part) for part in box.split(","))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"рамка должна быть четырьмя числами: {box}") from exc
    if len(values) != 4:
        raise HTTPException(status_code=400, detail="рамка задаётся как x1,y1,x2,y2 в долях кадра")
    x1, y1, x2, y2 = (min(max(value, 0.0), 1.0) for value in values)
    if x2 - x1 < 0.02 or y2 - y1 < 0.02:
        raise HTTPException(status_code=400, detail="рамка слишком мала")
    return x1, y1, x2, y2


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

    def _quality() -> dict | None:
        """Надёжность выдачи: доли верных ответов в top-1 и top-5 по последним сквозным прогонам.

        ТЗ требует показывать уверенность для топ-1 и топ-5 вместе с карточкой (в интерфейсе не
        обязательно, достаточно в API). Числа берутся из сводки прогонов, которую готовит
        `make report`; без неё поле просто отсутствует."""
        path = get_paths().artifacts_dir / "eval" / "summary.json"
        if not path.exists():
            return None
        stamp = path.stat().st_mtime
        cached = state.get("quality")
        if cached and cached[0] == stamp:
            return cached[1]
        summary = json.loads(path.read_text(encoding="utf-8"))
        runs = [r for r in summary.get("runs", []) if r.get("kind") == "сервис" and r.get("top1") is not None]
        splits: dict[str, dict] = {}
        for run in sorted(runs, key=lambda r: r.get("finished_at") or ""):  # остаётся самый свежий по выборке
            splits[run.get("split") or "неизвестно"] = {
                "run": run["run"],
                "queries": run.get("queries"),
                "top1": round(run["top1"], 4),
                "top5": round(run["top5"], 4) if run.get("top5") is not None else None,
                "measured_at": run.get("finished_at"),
            }
        value = {"measured_on": summary.get("generated_at"), "splits": splits} if splits else None
        state["quality"] = (stamp, value)
        return value

    @app.post("/v1/scan")
    def scan(image: UploadFile = File(...), box: str | None = Form(None)) -> dict:
        """``box`` — рамка, которую указал пользователь: «x1,y1,x2,y2» в долях кадра (0…1).

        Без неё бутылку выбирает детектор; с ней выбор не нужен — именно на нём теряется
        больше всего точности (ARCHITECTURE.md, слой 1)."""
        body = state["scanner"].scan(_read_image(image), _parse_box(box)).to_dict()
        quality = _quality()
        return {**body, "quality": quality} if quality else body

    @app.get("/v1/wines/{slug}")
    def wine(slug: str) -> dict:
        card = state["scanner"].cards.get(slug)
        if card is None:
            raise HTTPException(status_code=404, detail="вино не найдено")
        return card

    @app.get("/v1/wines/{slug}/analogs")
    def wine_analogs(slug: str, limit: int = 6) -> dict:
        """Аналоги других виноделен с объяснением по совпавшим полям (без LLM)."""
        from winescan.product.analogs import AnalogFinder

        scanner = state["scanner"]
        if slug not in scanner.cards:
            raise HTTPException(status_code=404, detail="вино не найдено")
        if "analogs" not in state:
            state["analogs"] = AnalogFinder(scanner.cards)
        return {"slug": slug, "analogs": [a.as_dict() for a in state["analogs"].find(slug, limit=max(1, min(limit, 20)))]}

    @app.get("/v1/sommelier/questions")
    def sommelier_questions() -> dict:
        from winescan.product.sommelier import questions

        return {"questions": questions()}

    @app.post("/v1/sommelier/suggest")
    def sommelier_suggest(request: dict) -> dict:
        """Подбор 3 вин по ответам на вопросы: {"dish", "category", "sweetness", "body", "region", "exclude_slugs"}."""
        from winescan.product.sommelier import Sommelier, SommelierRequest

        if "sommelier" not in state:
            state["sommelier"] = Sommelier(state["scanner"].cards)
        allowed = {"dish", "category", "sweetness", "body", "region"}
        params = {k: v for k, v in request.items() if k in allowed and isinstance(v, str) and v}
        params["exclude_slugs"] = tuple(s for s in request.get("exclude_slugs", []) if isinstance(s, str))
        try:
            suggestions = state["sommelier"].suggest(SommelierRequest(**params), limit=3)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"suggestions": [s.as_dict() for s in suggestions],
                "disclaimer": "Информация о винах каталога. 18+. Чрезмерное употребление алкоголя вредит вашему здоровью."}  # fmt: skip

    @app.get("/v1/metrics")
    def metrics() -> dict:
        """Сводка прогонов для страницы метрик. Файл готовит `make report`; сервис не зависит от
        пакета измерений (ARCHITECTURE.md, раздел 3), поэтому читает готовый JSON."""
        path = get_paths().artifacts_dir / "eval" / "summary.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="сводка прогонов не собрана: make report")
        return json.loads(path.read_text(encoding="utf-8"))

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
