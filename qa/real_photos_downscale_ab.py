"""A/B-обёртка над боевым приложением для приёмки `apps/api/app/cv/downscale.py`.

Зачем отдельный модуль. Порог уменьшения зашит в код константой `SCAN_MAX_SIDE`
и подставлен значением по умолчанию аргумента (`max_side: int = SCAN_MAX_SIDE`),
поэтому «выключить уменьшение» подменой константы нельзя, а править `apps/api`
ради замера нельзя по границам задачи. Обёртка подменяет ИМЯ, которое зовёт роутер
(`app.routers.scan.downscale_to_max_side`), и ничего в `apps/api` не меняет.

  QA_SCAN_MAX_SIDE=1024  — как сейчас в main (вариант A)
  QA_SCAN_MAX_SIDE=0     — уменьшения нет вовсе, кадр уходит в движок как есть (вариант B)
  QA_SCAN_MAX_SIDE=1600  — любой другой порог (сколько стоит и что даёт)
  QA_DOWNSCALE_LOG=<jsonl> — по кадру: мс самого шага, байты и стороны до/после

Запуск (из apps/api, чтобы `app.main` импортировался как на стенде):
  PYTHONPATH=<корень репо> QA_SCAN_MAX_SIDE=1024 .venv/bin/uvicorn qa.downscale_ab_app:app --port 8794
"""
from __future__ import annotations

import io
import json
import os
import threading
import time

from app.cv import downscale as _downscale_mod
from app.main import app  # noqa: F401  — то же самое приложение, что на стенде
from app.routers import scan as _scan_router

MAX_SIDE = int(os.environ.get("QA_SCAN_MAX_SIDE", str(_downscale_mod.SCAN_MAX_SIDE)))
LOG_PATH = os.environ.get("QA_DOWNSCALE_LOG") or ""

_original = _downscale_mod.downscale_to_max_side
_lock = threading.Lock()
_rows: list[dict] = []


def _side(data: bytes) -> list[int] | None:
    """Стороны кадра по заголовку (без декодирования пикселей) — вне замера времени."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return [im.width, im.height]
    except Exception:
        return None


def _probe(image_bytes: bytes, max_side: int | None = None) -> bytes:
    started = time.perf_counter()
    out = image_bytes if MAX_SIDE <= 0 else _original(image_bytes, MAX_SIDE)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    row = {
        "max_side": MAX_SIDE,
        "ms": round(elapsed_ms, 2),
        "bytes_in": len(image_bytes),
        "bytes_out": len(out),
        "size_in": _side(image_bytes),
        "size_out": _side(out),
        "changed": out is not image_bytes,
    }
    with _lock:
        _rows.append(row)
        if LOG_PATH:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


_scan_router.downscale_to_max_side = _probe


# --- след VLM-ветки: что увидела модель и чью сторону взял CV_FUSION_CHOOSE -----------
VLM_TRACE = os.environ.get("QA_VLM_TRACE") or ""
if VLM_TRACE:
    from app.cv import service as _service
    from app.cv import vision_llm as _vision_llm

    _orig_read = _vision_llm.read_label_fields_or_raise
    _orig_choose = _service._choose_fusion_result
    _pending: dict = {}

    def _traced_read(image_bytes: bytes, *args, **kwargs):
        started = time.perf_counter()
        try:
            fields = _orig_read(image_bytes, *args, **kwargs)
            err = None
        except Exception as exc:  # предохранитель/таймаут/сбой шлюза
            fields, err = None, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _pending.setdefault("model", []).append({
                "bytes_in": len(image_bytes),
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "fields": fields,
                "error": err,
            })
        return fields

    def _traced_choose(model_result, local_result, mode: str):
        chosen, side = _orig_choose(model_result, local_result, mode)
        row = {
            "max_side": MAX_SIDE,
            "mode": mode,
            "chosen_side": side,
            "model_slug": model_result.ranked[0].slug if model_result.ranked else None,
            "model_confident": bool(getattr(model_result, "confident", False)),
            "model_score": round(model_result.ranked[0].final_score, 4) if model_result.ranked else None,
            "local_slug": local_result.ranked[0].slug if local_result.ranked else None,
            "local_confident": bool(getattr(local_result, "confident", False)),
            "local_score": round(local_result.ranked[0].final_score, 4) if local_result.ranked else None,
            "model_calls": _pending.pop("model", []),
        }
        with _lock:
            with open(VLM_TRACE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return chosen, side

    _vision_llm.read_label_fields_or_raise = _traced_read
    _service._choose_fusion_result = _traced_choose


@app.get("/qa/downscale-stats")
def _stats() -> dict:
    with _lock:
        rows = list(_rows)
    times = sorted(r["ms"] for r in rows)
    def pct(p: float) -> float:
        if not times:
            return 0.0
        k = (len(times) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(times) - 1)
        return round(times[lo] + (times[hi] - times[lo]) * (k - lo), 2)
    return {
        "max_side": MAX_SIDE,
        "n": len(rows),
        "ms_p50": pct(0.5),
        "ms_p95": pct(0.95),
        "ms_max": round(times[-1], 2) if times else 0.0,
        "ms_sum": round(sum(times), 1),
        "changed": sum(1 for r in rows if r["changed"]),
    }
