"""Сводная таблица экспериментов из artifacts/eval/*/metrics.json и *sweep*.json.

Запуск: ``python -m winescan.eval.report --out docs/RESULTS.md``

Таблица генерируется, а не пишется руками, чтобы цифры в документации совпадали с прогонами.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from winescan.config import PROJECT_ROOT, get_paths


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _best_sweep(path: Path) -> tuple[str, dict] | None:
    if not path.exists():
        return None
    sweep = json.loads(path.read_text(encoding="utf-8"))
    weight = max(sweep, key=lambda w: sweep[w]["all"]["top1_accuracy"])
    return weight, sweep[weight]


def render(eval_dir: Path) -> str:
    runs = sorted(p.parent for p in eval_dir.glob("*/metrics.json"))
    lines = [
        "# Результаты экспериментов",
        "",
        f"Сгенерировано `python -m winescan.eval.report` {datetime.now():%Y-%m-%d %H:%M}. Не редактировать руками.",
        "Определения метрик — `src/winescan/eval/metrics.py`; подвыборки — docs/DATA.md, раздел 6.",
        "",
        "## Прогоны",
        "",
        "| прогон | кроп | OCR | top-1 | top-5 | top-1 похожие (pHash) | top-1 общий эталон | F1@1 | детекция, мс | OCR, мс |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    sweeps = []
    for run in runs:
        metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
        if "all" not in metrics:
            continue
        latency = metrics.get("latency_ms", {})
        lines.append(
            f"| `{run.name}` | {metrics['crop']} | {'да' if metrics.get('ocr') else 'нет'} | "
            f"{_fmt(metrics['all']['top1_accuracy'])} | {_fmt(metrics['all']['top5_accuracy'])} | "
            f"{_fmt(metrics['in_phash_group']['top1_accuracy'])} | {_fmt(metrics['shares_image']['top1_accuracy'])} | "
            f"{_fmt(metrics['all']['f1_at_1_best']['f1'])} | {latency.get('detect_mean', 0):.0f} | {latency.get('ocr_mean', 0):.0f} |"
        )
        for sweep_file, label in (("local_rerank_sweep_top5.json", "SIFT top-5"), ("rerank_sweep.json", "текст OCR")):
            best = _best_sweep(run / sweep_file)
            if best:
                sweeps.append((run.name, label, *best))
    lines += [
        "",
        "## Переранжирование (лучший вес по top-1 на том же прогоне)",
        "",
        "| прогон | сигнал | вес | top-1 | top-5 | top-1 похожие | top-1 общий эталон |",
        "|---|---|---|---|---|---|---|",
    ]
    for run_name, label, weight, metrics in sweeps:
        lines.append(
            f"| `{run_name}` | {label} | {weight} | {_fmt(metrics['all']['top1_accuracy'])} | "
            f"{_fmt(metrics['all']['top5_accuracy'])} | {_fmt(metrics['in_phash_group']['top1_accuracy'])} | "
            f"{_fmt(metrics['shares_image']['top1_accuracy'])} |"
        )
    lines += ["", "Синтетика оптимистична (в кадре пиксели эталона), см. ARCHITECTURE.md, раздел 4.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Сводная таблица экспериментов")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "docs" / "RESULTS.md")
    args = parser.parse_args(argv)
    text = render(get_paths().artifacts_dir / "eval")
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
