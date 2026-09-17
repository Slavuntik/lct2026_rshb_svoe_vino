"""Тесты cv/families.py — загрузчик переписи near-dup семей (contracts/image-scan.md
v0.4.7 п.1, agents/G4-family-gap.md). Чистые юниты, без энкодера/OCR/Qdrant — быстрые.
"""
from __future__ import annotations

import json

from cv import config
from cv.families import default_families_path, load_family_by_slug


def test_load_family_by_slug_missing_file_returns_empty(tmp_path):
    assert load_family_by_slug(tmp_path / "does-not-exist.json") == {}


def test_load_family_by_slug_malformed_json_returns_empty(tmp_path):
    path = tmp_path / "families.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_family_by_slug(path) == {}


def _write(tmp_path, data: dict):
    path = tmp_path / "families.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_family_by_slug_flat_schema_with_slugs_key(tmp_path):
    """`{family_id: {"slugs": [...], ...}}` — схема F3 боевого датасета (16.09.2026)."""
    path = _write(tmp_path, {
        "fam-a": {"slugs": ["a1", "a2"], "differentiator": "год"},
        "fam-b": {"slugs": ["b1"]},
    })
    assert load_family_by_slug(path) == {"a1": "fam-a", "a2": "fam-a", "b1": "fam-b"}


def test_load_family_by_slug_wrapped_in_families_key(tmp_path):
    """`{"families": {family_id: [...]}}` — старая встроенная обёртка (до миграции F3
    на отдельный файл), поддерживается на случай отката/старых фикстур."""
    path = _write(tmp_path, {"families": {"fam-a": ["a1", "a2"]}})
    assert load_family_by_slug(path) == {"a1": "fam-a", "a2": "fam-a"}


def test_load_family_by_slug_bare_list_value_without_slugs_key(tmp_path):
    """Значение семьи — голый список (не dict с ключом slugs) — тоже валидная форма."""
    path = _write(tmp_path, {"fam-a": ["a1", "a2", "a3"]})
    assert load_family_by_slug(path) == {"a1": "fam-a", "a2": "fam-a", "a3": "fam-a"}


def test_load_family_by_slug_empty_object_returns_empty(tmp_path):
    path = tmp_path / "families.json"
    path.write_text("{}", encoding="utf-8")
    assert load_family_by_slug(path) == {}


def test_load_family_by_slug_non_object_json_returns_empty(tmp_path):
    path = tmp_path / "families.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_family_by_slug(path) == {}


# --- default_families_path(): CV_FAMILIES_JSON env > $CASE_DATA_DIR/families.json ---


def test_default_families_path_uses_env_when_set(monkeypatch, tmp_path):
    custom = tmp_path / "custom-families.json"
    monkeypatch.setenv("CV_FAMILIES_JSON", str(custom))
    assert default_families_path() == custom


def test_default_families_path_falls_back_to_case_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("CV_FAMILIES_JSON", raising=False)
    monkeypatch.setattr(config, "CASE_DATA_DIR", tmp_path)
    assert default_families_path() == tmp_path / "families.json"
