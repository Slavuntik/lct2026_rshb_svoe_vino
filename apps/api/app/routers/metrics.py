"""GET /v1/metrics/scan (contracts/image-scan.md v0.4, кейс ЛЦТ) — публичная
сводка последнего eval-прогона CV. Без авторизации: та же логика, что и
/scan/photo — питч/демо кейса может дёрнуть эту ручку независимо от
пользовательской сессии, и внешнего риска в самой метрике нет (никаких ПД).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings_dep
from ..cv.eval_report import read_eval_report
from ..cv.interface import ImageIndex
from ..deps import get_image_index_dep
from ..schemas import ScanMetricsResponse

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/scan", response_model=ScanMetricsResponse)
def scan_metrics(
    settings: Settings = Depends(get_settings_dep),
    image_index: ImageIndex = Depends(get_image_index_dep),
) -> ScanMetricsResponse:
    report = read_eval_report(settings.cv_eval_report_path)
    if report is None:
        # Честно ничего не выдумываем — до первого eval-прогона (датасет
        # кейса ещё не приехал) все метрики null, index_version — от живого
        # индекса как единственное, что реально известно прямо сейчас.
        return ScanMetricsResponse(
            index_version=getattr(image_index, "index_version", None) or settings.cv_index_version,
        )
    return ScanMetricsResponse(
        index_version=report.get("index_version")
        or getattr(image_index, "index_version", None)
        or settings.cv_index_version,
        f1_top1=report.get("f1_top1"),
        f1_top5=report.get("f1_top5"),
        match_rate=report.get("match_rate"),
        eval_set=report.get("eval_set"),
        measured_at=report.get("measured_at"),
    )
