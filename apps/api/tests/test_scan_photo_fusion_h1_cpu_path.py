"""agents/H1-cpu-path.md — гейт «не подтверждена винодельня» на уровне apps/api:
`Settings.cv_fusion_unconfirmed_winery_w`/`cv_fusion_ocr_unconfirmed_w` и их
маршрутизация в `app/cv/service.py::_run_photo_scan_fusion` по РАЗРЕШЁННОМУ
`text_source` запроса (не по сырому `CV_FUSION_TEXT_SOURCE`). Арифметика самого
гейта (`recall`/`rel`/инвариант `final_score`) — уже покрыта юнитами
`packages/cv/tests/test_text_fusion.py::test_fuse_*`; здесь — только проводка:
apps/api передаёт ПРАВИЛЬНЫЙ вес `cv.text_fusion.fuse()` в зависимости от того,
ЧЕМ реально был прочитан текст этого запроса.

Переиспользует обвязку test_scan_photo_fusion.py (`_enable_fusion`/
`_FusionImageIndex`/`_m`) и test_scan_photo.py (`_photo`/`_SpyLabelVerifier`) —
тот же приём кросс-импорта приватных тестовых хелперов, что уже использует
test_scan_photo_fusion.py само (`from tests.test_scan_photo import _photo,
_SpyLabelVerifier`).
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m

_CATALOG_ROWS = [
    {"Slug": "confirmed", "Название вина": "Крю Лермонт", "Винодельня": "Фанагория"},
    {"Slug": "unconfirmed", "Название вина": "Крю Лермонт Резерв", "Винодельня": "Другая Винодельня"},
]
_OCR_TEXT = "фанагория крю лермонт"  # называет винодельню "confirmed", НЕ называет "unconfirmed"


# --------------------------------------------------------------------------------------
# Settings: дефолты и env-переопределение (config.py)
# --------------------------------------------------------------------------------------


def test_default_winery_gate_settings_match_brief_values():
    from app.config import Settings

    settings = Settings()
    assert settings.cv_fusion_unconfirmed_winery_w == 1.0  # "как сейчас" — без переданного winery_index нет эффекта
    assert settings.cv_fusion_ocr_unconfirmed_w == 0.5  # "текст в полсилы"


def test_winery_gate_settings_env_vars_override_defaults(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("CV_FUSION_UNCONFIRMED_WINERY_W", "0.7")
    monkeypatch.setenv("CV_FUSION_OCR_UNCONFIRMED_W", "0.3")
    settings = Settings()
    assert settings.cv_fusion_unconfirmed_winery_w == 0.7
    assert settings.cv_fusion_ocr_unconfirmed_w == 0.3


# --------------------------------------------------------------------------------------
# Проводка: text_source=="ocr" -> cv_fusion_ocr_unconfirmed_w; vlm* -> cv_fusion_unconfirmed_winery_w
# --------------------------------------------------------------------------------------


def test_ocr_source_applies_winery_gate_by_default(client: TestClient, app, tmp_path, monkeypatch):
    """Дефолтная конфигурация (CV_FUSION_TEXT_SOURCE=ocr, никакой VLM не
    настроен) -> `text_source` этого запроса РАЗРЕШАЕТСЯ в "ocr" -> кандидат с
    подтверждённой винодельней обгоняет кандидата с равным CV, но
    неподтверждённой винодельней."""
    idx = _FusionImageIndex([_m("confirmed", 0.80), _m("unconfirmed", 0.80)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=_CATALOG_ROWS, cv_fusion_w=0.2)
    assert app.state.settings.cv_fusion_text_source == "ocr"

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    scores = {m["slug"]: m["score"] for m in r.json()["matches"]}
    assert scores["confirmed"] > scores["unconfirmed"]


def test_ocr_unconfirmed_w_env_var_reaches_fuse_end_to_end(client: TestClient, app, tmp_path, monkeypatch):
    """Не только читается в Settings — РЕАЛЬНО доходит до `cv.text_fusion.fuse()`
    через service.py: вес 0.0 обнуляет текстовый вклад неподтверждённого
    кандидата целиком, final_score схлопывается ровно к cv_score."""
    idx = _FusionImageIndex([_m("confirmed", 0.80), _m("unconfirmed", 0.80)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_CATALOG_ROWS,
        cv_fusion_w=0.2, cv_fusion_ocr_unconfirmed_w=0.0,
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    unconfirmed_score = next(m["score"] for m in r.json()["matches"] if m["slug"] == "unconfirmed")
    assert unconfirmed_score == pytest.approx(0.80)  # rel*0.0 = 0.0 -> final = cv_score ровно


def test_winery_gate_only_applies_when_resolved_source_is_ocr_not_vlm(
    client: TestClient, app, tmp_path, monkeypatch
):
    """agents/H1-cpu-path.md: 'для vlm-источников гейт вреден' — offline
    95.2% -> 93.5%, поэтому apps/api передаёт `cv_fusion_unconfirmed_winery_w`
    (дефолт 1.0, без эффекта) для vlm/vlm_local/vlm_both, НЕ
    `cv_fusion_ocr_unconfirmed_w`. Один и тот же текст читается ОБОИМИ путями
    (spy OCR / фиктивная VLM) — единственная переменная между прогонами —
    `CV_FUSION_TEXT_SOURCE`."""
    from app.cv import service as service_module

    def _unconfirmed_score(*, text_source: str) -> float:
        idx = _FusionImageIndex([_m("confirmed", 0.80), _m("unconfirmed", 0.80)])
        app.state.image_index = idx
        if text_source == "ocr":
            app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)
            _enable_fusion(
                app, tmp_path, monkeypatch, catalog_rows=_CATALOG_ROWS,
                cv_fusion_w=0.2, cv_fusion_text_source="ocr",
            )
        else:
            app.state.label_verifier = _SpyLabelVerifier(ocr_text="")  # OCR сам не читает ничего
            # Тимлид 22.09 (расширение брифа scan-budget, п.8): подмена целится в
            # read_label_or_raise() — см. test_scan_photo_text_source_log.py.
            monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: _OCR_TEXT)
            _enable_fusion(
                app, tmp_path, monkeypatch, catalog_rows=_CATALOG_ROWS,
                cv_fusion_w=0.2, cv_fusion_text_source="vlm",
                vision_llm_url="http://fake-gateway.invalid", vision_llm_key="fake-key",
            )
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
        assert r.status_code == 200, r.text
        return next(m["score"] for m in r.json()["matches"] if m["slug"] == "unconfirmed")

    ocr_score = _unconfirmed_score(text_source="ocr")
    vlm_score = _unconfirmed_score(text_source="vlm")
    assert vlm_score > ocr_score, "vlm-источник не должен урезать текст неподтверждённой винодельни"


def test_vlm_fallback_to_ocr_text_still_applies_the_gate(client: TestClient, app, tmp_path, monkeypatch):
    """`CV_FUSION_TEXT_SOURCE=vlm`, но VLM не ответила (нет `VISION_LLM_URL`/
    `VISION_LLM_KEY`) -> слияние честно падает на тексте OCR (см.
    `_fusion_text_and_vectors`), `text_source` ЭТОГО запроса — фактически "ocr"
    -> гейт обязан сработать ТОЖЕ, несмотря на настроенный режим "vlm" (важно
    качество текста, который РЕАЛЬНО участвует, не то, что было сконфигурировано)."""
    idx = _FusionImageIndex([_m("confirmed", 0.80), _m("unconfirmed", 0.80)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)  # OCR — единственный, кто ответил
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_CATALOG_ROWS,
        cv_fusion_w=0.2, cv_fusion_text_source="vlm",  # vlm_url/key НЕ заданы -> реального вызова VLM не будет
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    scores = {m["slug"]: m["score"] for m in r.json()["matches"]}
    assert scores["confirmed"] > scores["unconfirmed"]
