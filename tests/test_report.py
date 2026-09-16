import json

from winescan.eval.report import render, summary


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


def test_summary_collects_search_and_service_runs(tmp_path):
    search = tmp_path / "synth_v2__index__detector"
    search.mkdir()
    (search / "metrics.json").write_text(json.dumps({
        "split": "synth_v2", "crop": "detector", "queries": 2103,
        "all": {"top1_accuracy": 0.64, "top5_accuracy": 0.78}, "in_phash_group": {"top1_accuracy": 0.6},
    }), encoding="utf-8")  # fmt: skip
    service = tmp_path / "scanner__public__default"
    service.mkdir()
    (service / "metrics.json").write_text(json.dumps({
        "split": "public", "crop": "сервис", "queries": 3,
        "decision": {"answered_correct": 1.0}, "latency_ms": {"total_p50": 1020.0, "total_p95": 1590.0},
    }), encoding="utf-8")  # fmt: skip
    (tmp_path / "пусто").mkdir()

    runs = {run["run"]: run for run in summary(tmp_path)["runs"]}

    assert set(runs) == {"synth_v2__index__detector", "scanner__public__default"}
    assert runs["synth_v2__index__detector"]["kind"] == "поиск" and runs["synth_v2__index__detector"]["top1"] == 0.64
    assert runs["scanner__public__default"]["kind"] == "сервис" and runs["scanner__public__default"]["latency_p95_ms"] == 1590.0
    assert runs["scanner__public__default"]["top1"] is None
