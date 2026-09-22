"""agents/ML-1-*.md (задача 2) — алиас винодельни в гейте подтверждения, сквозная
проводка `CV_WINERY_ALIASES_JSON` -> `app/cv/service.py::_fusion_winery_index()` ->
`cv.text_fusion.load_winery_index()` через реальный `/v1/scan/photo`.

Арифметика самого объединения токенов (recall/idf) уже покрыта юнитами
`packages/cv/tests/test_text_fusion.py::test_load_winery_index_*` — здесь только
ПРОВОДКА: тот же реальный сценарий (слаг 93.97, reports/ml-lead-plan.md — каталог
пишет ОДНОГО производителя ДВУМЯ строками «Винодельня»), но через полный
`/v1/scan/photo`, с тем же приёмом кросс-импорта `_enable_fusion`/
`_FusionImageIndex`/`_m`/`_photo`/`_SpyLabelVerifier`, что
test_scan_photo_fusion_h1_cpu_path.py.
"""
from __future__ import annotations

import json

from starlette.testclient import TestClient

from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m

_GOLUBITSKOE_CATALOG_ROWS = [
    {"Slug": "truth", "Название вина": "Golubitskoe Estate Chardonnay", "Винодельня": "Поместье Голубицкое"},
    {"Slug": "rival", "Название вина": "Golubitskoe Estate Reserve", "Винодельня": "Golubitskoe Estate"},
]
_GOLUBITSKOE_ALIASES = {"groups": [
    {"strings": ["Поместье Голубицкое", "Golubitskoe Estate"], "anchor_token": "golubitskoe",
     "note": "тест — тот же пример, что case-data/winery_aliases.json"},
]}
_OCR_TEXT = "Golubitskoe Estate Chardonnay"  # текст этикетки, как в разборе промаха 93.97


def _golubitskoe_flat_top1(client: TestClient, app, tmp_path, monkeypatch, *, winery_aliases):
    idx = _FusionImageIndex([_m("truth", 0.8678), _m("rival", 0.8676)])  # CV — почти ничья, как в разборе
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_GOLUBITSKOE_CATALOG_ROWS,
        winery_aliases=winery_aliases, cv_fusion_w=0.3, cv_fusion_text_source="ocr",
    )
    r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.status_code == 200, r.text
    return r.json()["slug"]


def test_without_aliases_file_gate_penalizes_truth_by_its_own_spelling(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """Баг ДО этой правки (reports/ml-lead-plan.md, промах 93.97): без файла
    алиасов recall истины по её СОБСТВЕННОЙ строке «Винодельня» ниже пола -> гейт
    срезает её текстовое преимущество (масса и так уже отдаёт ей победу), и
    побеждает конкурент с более "удачным" написанием."""
    assert _golubitskoe_flat_top1(client, app, tmp_path, monkeypatch, winery_aliases=None) == "rival"


def test_with_aliases_file_truth_wins(client: TestClient, app, tmp_path, monkeypatch):
    """agents/ML-1-*.md, задача 2: с проверенным вручную алиасом оба написания
    подтверждают друг друга (recall становится равным и выше пола для обоих) —
    гейт больше не наказывает истину, побеждает она."""
    assert _golubitskoe_flat_top1(
        client, app, tmp_path, monkeypatch, winery_aliases=_GOLUBITSKOE_ALIASES,
    ) == "truth"


def test_winery_alias_file_does_not_affect_slugs_outside_any_group(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """Регрессия (brief п.3): файл алиасов ЕСТЬ (для Голубицкого), но эта пара
    слагов его не касается — гейт «не подтверждена винодельня» ведёт себя как в
    agents/H1-cpu-path.md (test_scan_photo_fusion_h1_cpu_path.py), без изменений."""
    rows = [
        {"Slug": "confirmed", "Название вина": "Крю Лермонт", "Винодельня": "Фанагория"},
        {"Slug": "unconfirmed", "Название вина": "Крю Лермонт Резерв", "Винодельня": "Другая Винодельня"},
    ]
    idx = _FusionImageIndex([_m("confirmed", 0.80), _m("unconfirmed", 0.80)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="фанагория крю лермонт")
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=rows, winery_aliases=_GOLUBITSKOE_ALIASES,
        cv_fusion_w=0.2, cv_fusion_text_source="ocr",
    )
    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    scores = {m["slug"]: m["score"] for m in r.json()["matches"]}
    assert scores["confirmed"] > scores["unconfirmed"]


def test_winery_aliases_env_var_resolves_independently_of_case_data_dir(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """`CV_WINERY_ALIASES_JSON` резолвится независимо от `CASE_DATA_DIR` (тот же
    принцип живого резолва, что `CV_FAMILIES_JSON`) — путь ВНЕ
    tmp_path/CASE_DATA_DIR тоже подхватывается. Env выставляется ПОСЛЕ
    `_enable_fusion()` — та сама ставит `CV_WINERY_ALIASES_JSON` (на заведомо
    несуществующий путь, раз `winery_aliases` не передан), последний
    `monkeypatch.setenv()` в тесте побеждает."""
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    aliases_path = other_dir / "custom-winery-aliases.json"
    aliases_path.write_text(json.dumps(_GOLUBITSKOE_ALIASES, ensure_ascii=False), encoding="utf-8")

    idx = _FusionImageIndex([_m("truth", 0.8678), _m("rival", 0.8676)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text=_OCR_TEXT)
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_GOLUBITSKOE_CATALOG_ROWS,
        cv_fusion_w=0.3, cv_fusion_text_source="ocr",
    )
    monkeypatch.setenv("CV_WINERY_ALIASES_JSON", str(aliases_path))  # переопределяет путь _enable_fusion

    r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "truth"


def test_missing_winery_aliases_file_degrades_to_previous_behavior(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """brief п.3: "файл алиасов отсутствует — старое поведение" — `CV_WINERY_ALIASES_JSON`
    указывает на несуществующий файл -> тот же результат, что вовсе без env (см.
    test_without_aliases_file_gate_penalizes_truth_by_its_own_spelling)."""
    monkeypatch.setenv("CV_WINERY_ALIASES_JSON", str(tmp_path / "does-not-exist.json"))
    assert _golubitskoe_flat_top1(client, app, tmp_path, monkeypatch, winery_aliases=None) == "rival"
