"""Задача тимлида 22.09 (reports/devops-stand-vlm.md, находка "источник текста
этикетки vlm/ocr нигде не виден снаружи процесса" — ни в rich-ответе, ни в архиве,
ни в логах на успехе; только warning на сбой/фолбэк, vision_llm.py).

`PhotoScanResult.text_source`/`label_text` (CV_FUSION, app/cv/service.py) в HTTP-ответ
по-прежнему НЕ идут (контракт не меняется) — только в INFO-лог `app.routers.scan`,
и только источник/длина/время, без содержимого текста и без ключей шлюза. Обвязка —
та же, что test_scan_photo_fusion_ml1_merge_text.py: `_enable_fusion`/`_FusionImageIndex`/
`_m` (tests/test_scan_photo_fusion.py) + `_photo`/`_SpyLabelVerifier` (tests/test_scan_photo.py).
"""
from __future__ import annotations

import logging

from starlette.testclient import TestClient

from app.cv import service as service_module
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m

LOGGER_NAME = "app.routers.scan"


def _records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER_NAME]


def test_rich_scan_logs_ocr_source_len_and_ms(client: TestClient, app, tmp_path, monkeypatch, caplog):
    """Ни одна модель не настроена -> фолбэк "ocr" (_fusion_text_and_vectors) — тоже
    успешное чтение, тоже должно попасть в лог."""
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    ocr_text = "СЕКРЕТНЫЙ ТЕКСТ ЭТИКЕТКИ КОТОРЫЙ НЕ ДОЛЖЕН ПОПАСТЬ В ЛОГ"
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=ocr_text)
    _enable_fusion(app, tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200

    [record] = _records(caplog)
    message = record.getMessage()
    assert "source=ocr" in message
    assert f"len={len(ocr_text)}" in message
    assert "ms=" in message
    assert ocr_text not in message, "без содержимого текста этикетки"


def test_rich_scan_logs_vlm_local_source_when_model_answers(client: TestClient, app, tmp_path, monkeypatch, caplog):
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label", lambda *a, **kw: "ТЕКСТ МОДЕЛИ, НЕ ЛОГИРОВАТЬ")
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200

    [record] = _records(caplog)
    message = record.getMessage()
    assert "source=vlm_local" in message
    assert "ТЕКСТ МОДЕЛИ" not in message, "без содержимого текста этикетки"


def test_no_log_when_fusion_disabled(client: TestClient, caplog):
    """text_source остаётся None вне CV_FUSION — логировать нечего (не путать
    отсутствие чтения этикетки с ошибкой)."""
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    assert _records(caplog) == []


def test_no_log_on_flat_mode(client: TestClient, app, tmp_path, monkeypatch, caplog):
    """flat/`/v1/eval/predict` — приватная выборка кейсодержателя, тот же принцип,
    что архив (contracts/image-scan.md v0.4.10) её не пишет — лог тоже молчит."""
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="ТЕКСТ")
    _enable_fusion(app, tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.status_code == 200
    assert _records(caplog) == []


def test_no_log_on_eval_predict_alias(client: TestClient, app, tmp_path, monkeypatch, caplog):
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="ТЕКСТ")
    _enable_fusion(app, tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        r = client.post(
            "/v1/eval/predict", files={"image": ("label.jpg", b"MOCKPHOTO:whatever", "image/jpeg")},
        )
    assert r.status_code == 200
    assert _records(caplog) == []
