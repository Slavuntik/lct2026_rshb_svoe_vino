"""app/rag/case_catalog.py — фолбэк-метаданные вина из каталога кейса
(contracts/image-scan.md v0.4.11 п.2, агент B8; отчёт волны —
reports/b8-candidates-card.md).

Юнит-тесты модуля в изоляции (без HTTP-слоя) — интеграционная проверка через
живой GET /wines/{id} и /scan/photo см. tests/test_wines.py/test_scan_photo.py.
Стиль — зеркало tests/test_case_catalog.py (тот же принцип: КАЖДЫЙ тест
использует свой `tmp_path`, `_load_catalog()` кэширует по пути — разные тесты
естественным образом не видят чужой кэш, monkeypatch CASE_DATA_DIR ДО первого
обращения к lookup()/case_data_dir() в теле теста)."""
from __future__ import annotations

import json

from app.rag import case_catalog


def _write_case_catalog(tmp_path, mapping: dict) -> None:
    (tmp_path / "case_catalog.json").write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False), encoding="utf-8"
    )


# --- источник метаданных: подмена файла фикстурой --------------------------

def test_lookup_returns_all_fields_from_fixture(tmp_path, monkeypatch):
    _write_case_catalog(tmp_path, {
        "case-test-wine": {
            "name": "Тестовое вино", "winery_name": "Тестовая винодельня",
            "region_name": "Крым", "grapes": ["Алиготе", "Кокур Белый"],
            "color": "Белое", "category": "Светло-соломенный",
            "description": "Свежее, с цитрусовыми нотами.",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("case-test-wine")

    assert result == case_catalog.CaseWine(
        name="Тестовое вино", winery_name="Тестовая винодельня", region_name="Крым",
        grapes=["Алиготе", "Кокур Белый"], color="Белое", category="Светло-соломенный",
        description="Свежее, с цитрусовыми нотами.",
    )


def test_lookup_returns_none_for_slug_missing_from_mapping(tmp_path, monkeypatch):
    _write_case_catalog(tmp_path, {"known-slug": {"name": "Известное"}})
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("unknown-slug") is None


def test_lookup_falls_back_to_slug_when_name_missing(tmp_path, monkeypatch):
    """Честная деградация имени (не пустая строка) — тот же принцип, что и
    app/cv/case_catalog.py::lookup для верификатора."""
    _write_case_catalog(tmp_path, {"case-no-name": {"winery_name": "Кто-то"}})
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("case-no-name")

    assert result.name == "case-no-name"
    assert result.winery_name == "Кто-то"


def test_lookup_defaults_missing_fields_to_empty(tmp_path, monkeypatch):
    """Только `name` есть — остальные текстовые поля пустые строки (не None,
    не KeyError), grapes — пустой список."""
    _write_case_catalog(tmp_path, {"case-bare": {"name": "Голое вино"}})
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("case-bare")

    assert result == case_catalog.CaseWine(
        name="Голое вино", winery_name="", region_name="", grapes=[], color="", category="", description="",
    )


# --- фолбэк без case-data ----------------------------------------------------

def test_lookup_returns_none_when_case_data_dir_has_no_case_catalog_json(tmp_path, monkeypatch):
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("any-slug-at-all") is None


def test_lookup_returns_none_on_malformed_json(tmp_path, monkeypatch):
    (tmp_path / "case_catalog.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("whatever") is None


def test_lookup_returns_none_when_mapping_key_absent_from_payload(tmp_path, monkeypatch):
    (tmp_path / "case_catalog.json").write_text(json.dumps({"count": 0}), encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("whatever") is None


# --- кэш: лениво, один раз, без утечки между разными CASE_DATA_DIR ----------

def test_lookup_does_not_leak_cache_between_different_case_data_dirs(tmp_path, monkeypatch):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_case_catalog(dir_a, {"shared-slug": {"name": "Вино А"}})
    _write_case_catalog(dir_b, {"shared-slug": {"name": "Вино Б"}})

    monkeypatch.setenv("CASE_DATA_DIR", str(dir_a))
    result_a = case_catalog.lookup("shared-slug")

    monkeypatch.setenv("CASE_DATA_DIR", str(dir_b))
    result_b = case_catalog.lookup("shared-slug")

    assert result_a.name == "Вино А"
    assert result_b.name == "Вино Б"


# --- source_url / thumb_url: чистые функции конвенции URL ------------------

def test_source_url_follows_vino_svoe_convention():
    """contracts/image-scan.md v0.4.11 п.3."""
    assert case_catalog.source_url("case-massandra-muskatel-belyy") == (
        "https://vino-svoe.ru/wines/case-massandra-muskatel-belyy"
    )


def test_thumb_url_points_at_case_thumbs_route():
    """contracts/image-scan.md v0.4.11 п.4 — то же имя маршрута, что
    app/routers/case_thumbs.py регистрирует под /v1."""
    assert case_catalog.thumb_url("some-slug") == "/v1/case-thumbs/some-slug.webp"


def test_case_data_dir_reads_env_live_with_default(monkeypatch):
    monkeypatch.setenv("CASE_DATA_DIR", "/tmp/custom-case-data")
    assert str(case_catalog.case_data_dir()) == "/tmp/custom-case-data"

    monkeypatch.delenv("CASE_DATA_DIR", raising=False)
    assert str(case_catalog.case_data_dir()) == "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"
