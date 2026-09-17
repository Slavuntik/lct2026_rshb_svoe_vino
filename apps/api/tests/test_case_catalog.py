"""app/cv/case_catalog.py — метаданные кандидатов верификатора из каталога
КЕЙСА (contracts/image-scan.md, "Дополнения v0.4.9" после e2e B5,
reports/b5-gate-v048.md §2 "Причина 3"; отчёт этой волны —
reports/b6-case-candidates.md).

Юнит-тесты модуля в изоляции (без HTTP-слоя) — интеграционные тесты через
живой /v1/scan/photo + _SpyLabelVerifier см. test_scan_photo.py (класс
поиска "case catalog metadata").

Каждый тест использует СВОЙ `tmp_path` (pytest даёт уникальную директорию на
тест) — `case_catalog._load_mapping()` кэширует по пути (`@lru_cache`), так
что разные тесты естественным образом не видят чужой кэш; тест на сам этот
факт — `test_lookup_does_not_leak_cache_between_different_case_data_dirs`.
"""
from __future__ import annotations

import json

from app.cv import case_catalog


def _write_slug_refs(tmp_path, mapping: dict) -> None:
    (tmp_path / "slug_refs.json").write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False), encoding="utf-8"
    )


# --- источник метаданных: подмена файла фикстурой --------------------------

def test_lookup_combines_name_and_winery_from_case_data_fixture(tmp_path, monkeypatch):
    """Контракт: "name (и winery) — из slug_refs.json (mapping[slug].name/
    .winery)". Подмена файла фикстурой (CASE_DATA_DIR -> tmp_path) — ровно
    сценарий, из-за которого q2 читался вхолостую (B5): без этой правки
    источник был бы RAG (get_by_id), которого этот slug не знает вовсе."""
    _write_slug_refs(tmp_path, {
        "case-massandra-muskatel-belyy": {"name": "Мускатель белый", "winery": "Массандра"},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("case-massandra-muskatel-belyy")

    assert result == case_catalog.CaseMetadata(name="Массандра Мускатель белый", vintage=None)


def test_lookup_returns_none_for_slug_missing_from_mapping(tmp_path, monkeypatch):
    """Файл ЕСТЬ, но конкретный slug каталог кейса не знает (например, CV
    нашёл позицию вне census'а) — честный `None`, не KeyError/исключение;
    вызывающий код (`app/cv/service.py::_verify_candidates`) обязан
    откатиться на `get_by_id()` для ИМЕННО этого слага."""
    _write_slug_refs(tmp_path, {"known-slug": {"name": "Известное", "winery": "Винодельня"}})
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("unknown-slug") is None


# --- фолбэк без case-data ----------------------------------------------------

def test_lookup_returns_none_when_case_data_dir_has_no_slug_refs_json(tmp_path, monkeypatch):
    """Контракт: "Фолбэк при отсутствии case-data — прежний путь get_by_id".
    `tmp_path` — гарантированно пустая директория (не полагаемся на то, что
    на этой машине case-data вообще нет — она ЕСТЬ, см. отчёт)."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("any-slug-at-all") is None


def test_lookup_returns_none_on_malformed_json(tmp_path, monkeypatch):
    """Битый файл -> {} (не исключение наружу) — тот же принцип честной
    деградации, что и `cv.families.load_family_by_slug()` в packages/cv."""
    (tmp_path / "slug_refs.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("whatever") is None


def test_lookup_returns_none_when_mapping_key_absent_from_payload(tmp_path, monkeypatch):
    """Файл валиден, но без ключа "mapping" (неожиданная форма) -> {} — та же
    честная деградация, не падение."""
    (tmp_path / "slug_refs.json").write_text(json.dumps({"generated_by": "x"}), encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    assert case_catalog.lookup("whatever") is None


# --- парсинг vintage ---------------------------------------------------------

def test_lookup_parses_vintage_from_slug_when_present(tmp_path, monkeypatch):
    """Контракт: "vintage — год (19xx/20xx) из слага ... если есть"."""
    _write_slug_refs(tmp_path, {
        "aligote-barrel-2024": {"name": "Алиготе Баррель, 2024", "winery": ""},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("aligote-barrel-2024")

    assert result.vintage == 2024
    assert result.name == "Алиготе Баррель, 2024"  # winery пустая строка -> не добавляется


def test_lookup_parses_vintage_from_name_when_absent_from_slug(tmp_path, monkeypatch):
    """Контракт: "... или имени, если есть" — реальный случай каталога кейса
    (`amelia`: слаг без года, `name: "Amelia, 2022"`)."""
    _write_slug_refs(tmp_path, {
        "amelia": {"name": "Amelia, 2022", "winery": "Amelia Estate"},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("amelia")

    assert result.vintage == 2022


def test_lookup_vintage_is_none_when_absent_from_both_slug_and_name(tmp_path, monkeypatch):
    """Большинство позиций каталога кейса винтажа вовсе не несут (проверено
    на реальном дампе: 116/2103 содержат год) — честный `None`, как и
    прежний путь `get_by_id()` для вин без урожая."""
    _write_slug_refs(tmp_path, {
        "massandra-heres": {"name": "Массандра Херес", "winery": "Массандра"},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("massandra-heres")

    assert result.vintage is None


def test_lookup_does_not_confuse_volume_digits_with_a_year(tmp_path, monkeypatch):
    """Год — РОВНО 4 цифры 19xx/20xx с границей не-цифра с обеих сторон (тот
    же regex, что packages/cv/cv/verify.py::_YEAR_RE) — "1500" (мл) не
    должен читаться как обрезок года."""
    _write_slug_refs(tmp_path, {
        "some-wine-1500ml": {"name": "Вино 1500 мл", "winery": ""},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    result = case_catalog.lookup("some-wine-1500ml")

    assert result.vintage is None


# --- кэш: лениво, один раз, без утечки между разными CASE_DATA_DIR ----------

def test_lookup_does_not_leak_cache_between_different_case_data_dirs(tmp_path, monkeypatch):
    """`_load_mapping()` кэширует по ПУТИ (`@lru_cache`), не глобальным
    флагом — "лениво при старте, один раз" по контракту не должно означать
    "первый увиденный CASE_DATA_DIR навсегда". Два РАЗНЫХ каталога в одном
    тесте (как было бы у двух разных тестов процесса) обязаны видеть каждый
    свои данные."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_slug_refs(dir_a, {"shared-slug": {"name": "Вино А", "winery": "Винодельня А"}})
    _write_slug_refs(dir_b, {"shared-slug": {"name": "Вино Б", "winery": "Винодельня Б"}})

    monkeypatch.setenv("CASE_DATA_DIR", str(dir_a))
    result_a = case_catalog.lookup("shared-slug")

    monkeypatch.setenv("CASE_DATA_DIR", str(dir_b))
    result_b = case_catalog.lookup("shared-slug")

    assert result_a.name == "Винодельня А Вино А"
    assert result_b.name == "Винодельня Б Вино Б"
