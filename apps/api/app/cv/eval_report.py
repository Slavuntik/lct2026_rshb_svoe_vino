"""Чтение последнего eval-отчёта CV (F1 top-1/top-5, match_rate, версия
индекса) — общий источник для GET /v1/metrics/scan и confidence-блока
rich-режима /v1/scan/photo (contracts/image-scan.md: "F1-цифры — с
последнего eval-прогона (версия индекса в манифесте)").

Путь — env CV_EVAL_REPORT_PATH (см. app/config.py); формата и самого файла
пока нет (эта задача — до приезда датасета кейса и до готовности
packages/cv/eval). Отсутствие файла — НЕ ошибка: честно возвращаем None,
вызывающая сторона обязана показать это как eval_missing, а не притвориться
нулевыми метриками (легко перепутать с "F1=0%").
"""
from __future__ import annotations

import json
from pathlib import Path

EvalReport = dict


def read_eval_report(path: str) -> EvalReport | None:
    """Ожидаемая форма файла (пишет `cv bench`/`cv eval` агента G, когда
    появится): {"index_version": str, "f1_top1": float, "f1_top5": float,
    "match_rate": float, "eval_set": str, "measured_at": str}. Путь может
    быть относительным — резолвится от текущего cwd процесса (тот же
    принцип, что у DATABASE_URL=sqlite:///./... — проект уже предполагает
    запуск из apps/api, см. reports/b-report.md)."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data
