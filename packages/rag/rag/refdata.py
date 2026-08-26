"""Загрузка справочников vines/ref (read-only) — стили, синонимы сортов, регионы.

Все функции кэшируют результат в процессе (lru_cache), т.к. справочники малы
и не меняются во время работы сервиса.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from rag import config


@lru_cache(maxsize=1)
def load_reference_styles() -> list[dict[str, Any]]:
    path = config.REF_DIR / "reference_styles.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("styles", [])


@lru_cache(maxsize=1)
def reference_styles_by_slug() -> dict[str, dict[str, Any]]:
    return {s["slug"]: s for s in load_reference_styles()}


@lru_cache(maxsize=1)
def load_grape_synonyms() -> dict[str, Any]:
    path = config.REF_DIR / "grape_synonyms.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def grape_slug_to_synonyms() -> dict[str, list[str]]:
    """slug сорта -> список написаний (synonyms + name), для расширения текста этикетки."""
    data = load_grape_synonyms()
    out: dict[str, list[str]] = {}
    for section in ("autochthonous", "hybrid_and_soviet", "international"):
        for entry in data.get(section, []) or []:
            slug = entry.get("slug")
            if not slug:
                continue
            names = set(entry.get("synonyms", []) or [])
            if entry.get("name"):
                names.add(entry["name"])
            out[slug] = sorted(names)
    return out


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    path = config.REF_DIR / "taxonomy.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def region_name_to_slug() -> dict[str, str]:
    tax = load_taxonomy()
    out = {}
    for r in tax.get("regions", []) or []:
        if r.get("name") and r.get("slug"):
            out[r["name"]] = r["slug"]
    return out


@lru_cache(maxsize=1)
def region_slug_to_name() -> dict[str, str]:
    return {v: k for k, v in region_name_to_slug().items()}


def normalize_region(value: str | None) -> str | None:
    """Приводит регион к slug независимо от того, пришло имя или slug."""
    if not value:
        return value
    name_to_slug = region_name_to_slug()
    if value in name_to_slug:
        return name_to_slug[value]
    # уже slug (или неизвестное значение) — возвращаем как есть
    return value
