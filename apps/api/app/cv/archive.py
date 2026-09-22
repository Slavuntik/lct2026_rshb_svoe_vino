"""Архив сканов из интерфейса (contracts/image-scan.md v0.4.10, env SCAN_ARCHIVE_DIR).

Каждый rich-скан раскладывается по уверенности системы:
  confident/ — показана одна карточка (гейт пройден);
  unsure/    — скор выше пола, но близкие кандидаты не разведены маржой (near-dup и т.п.);
  failed/    — ниже пола или совпадений нет: вина нет в каталоге, мусор или плохой кадр.
Это оценка САМОЙ системы, а не истина: в «confident» бывают уверенные ошибки, поэтому
контрольную выборку собирают только после ручной сверки — поле verified_slug в сайдкаре.

Фото перекодируется в JPEG без метаданных: EXIF телефона несёт GPS-координаты места
съёмки. Не удалось декодировать — сохраняется только сайдкар, исходные байты не пишутся.
Скриптовые режимы (flat, /v1/eval/predict) не архивируются намеренно: через них идёт
приватная контрольная выборка кейсодержателя.
"""
from __future__ import annotations

import io
import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .service import PhotoScanResult

logger = logging.getLogger(__name__)

BUCKETS = ("confident", "unsure", "failed")


def classify(result: PhotoScanResult, abs_floor: float) -> tuple[str, str]:
    """(корзина, причина) по исходу гейта."""
    if not result.not_in_catalog and result.slug:
        return "confident", "ocr_verified" if result.ocr_verified else "gate_passed"
    if result.top1_score is not None and result.top1_score >= abs_floor:
        return "unsure", "margin"
    return "failed", "no_match" if result.top1_score is None else "floor"


def to_clean_jpeg(data: bytes) -> tuple[bytes, dict] | None:
    """Декодирует фото, применяет EXIF-ориентацию и сохраняет JPEG без метаданных."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except Exception:
        pass
    try:
        with Image.open(io.BytesIO(data)) as im:
            orig_format = (im.format or "").lower()
            clean = ImageOps.exif_transpose(im).convert("RGB")
    except Exception:
        return None
    out = io.BytesIO()
    clean.save(out, "JPEG", quality=95)
    return out.getvalue(), {"orig_format": orig_format, "width": clean.width, "height": clean.height}


def archive_scan(
    root: str, data: bytes, result: PhotoScanResult, *, abs_floor: float, index_version: str | None
) -> Path:
    bucket, reason = classify(result, abs_floor)
    now = datetime.now(timezone.utc)
    scan_id = f"{now:%Y%m%dT%H%M%S}_{secrets.token_hex(3)}"
    base = Path(root)
    day_dir = base / bucket / f"{now:%Y%m%d}"
    day_dir.mkdir(parents=True, exist_ok=True)

    clean = to_clean_jpeg(data)
    meta: dict = {
        "id": scan_id,
        "ts": now.isoformat(),
        "bucket": bucket,
        "reason": reason,
        "predicted_slug": result.best_guess_slug,
        "shown_slug": result.slug,
        "top1_score": result.top1_score,
        "gap": result.gap,
        "ocr_verified": result.ocr_verified,
        # Задача тимлида 22.09 (reports/devops-stand-vlm.md: "источник текста этикетки
        # vlm/ocr нигде не виден снаружи процесса") — PhotoScanResult уже несёт оба поля
        # (app/cv/service.py, CV_FUSION), сюда просто прокидываются как есть; None вне
        # CV_FUSION (путь без чтения этикетки моделью/OCR). Поля вне contracts/image-
        # scan.md v0.4.10 ("<id>.json" перечисление) — см. "Предложения к контрактам" в
        # reports/backend-text-source.md, архитектор не спрошен.
        "text_source": result.text_source,
        "label_text": result.label_text,
        "not_in_catalog": result.not_in_catalog,
        "matches": result.matches[:5],
        "timing_ms": result.timing_ms,
        "index_version": index_version,
        "orig_bytes": len(data),
        "image_saved": clean is not None,
        "verified_slug": None,
    }
    if clean is not None:
        jpeg, info = clean
        meta.update(info)
        (day_dir / f"{scan_id}.jpg").write_bytes(jpeg)
    (day_dir / f"{scan_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8"
    )
    summary = {k: meta[k] for k in ("id", "ts", "bucket", "reason", "predicted_slug", "top1_score", "ocr_verified")}
    summary["path"] = str((day_dir / scan_id).relative_to(base))
    with open(base / "index.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False, default=float) + "\n")
    return day_dir / scan_id


def archive_scan_safe(root: str, data: bytes, result: PhotoScanResult, **kwargs) -> None:
    """Для фоновой задачи: сбой архива не должен ни ронять, ни задерживать ответ."""
    try:
        archive_scan(root, data, result, **kwargs)
    except Exception:
        logger.exception("скан не записан в архив %s", root)
