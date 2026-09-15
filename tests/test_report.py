import json

from winescan.eval.report import render


def test_render_lists_scanner_runs_with_decision_and_latency(tmp_path):
    run = tmp_path / "scanner__public__default"
    run.mkdir()
    metrics = {
        "split": "public",
        "crop": "сервис, рамок 3",
        "ocr": True,
        "queries": 3,
        "decision": {"answered_correct": 1.0, "answered_wrong": 0.0, "rejected": 0.0, "out_of_catalog_rejected": 1.0},
        "latency_ms": {"total_p50": 1628.4, "total_p95": 2238.0},
    }
    (run / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    text = render(tmp_path)

    assert "## Сквозные прогоны сервиса" in text
    assert "| `scanner__public__default` | 3 | — | 1.000 | 0.000 | 0.000 | 1.000 | 1628 | 2238 |" in text


def test_render_without_scanner_runs_has_no_scanner_section(tmp_path):
    assert "Сквозные прогоны сервиса" not in render(tmp_path)
