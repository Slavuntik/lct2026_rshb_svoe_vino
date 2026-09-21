#!/usr/bin/env python3
"""apps/api/scripts/build_case_catalog.py — дамп strapi кейса ->
case-data/case_catalog.json (agents/B8-candidates-card.md, contracts/
image-scan.md v0.4.11 п.2). Источник фолбэк-карточки для слагов кейса,
которых нет в нашем RAG-каталоге (122 из 2054 usable-слагов на снимке 21.09)
— читается `app/rag/case_catalog.py::lookup()`.

Запуск (из apps/api, venv активен):
    python scripts/build_case_catalog.py
Дефолт входа/выхода — CASE_DATA_DIR (env, тот же дефолт, что и у
`app/cv/case_catalog.py`/`app/rag/case_catalog.py`:
/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data). Вход —
<CASE_DATA_DIR>/strapi_output0709.csv, выход —
<CASE_DATA_DIR>/case_catalog.json (вне git — case-data/ в .gitignore корня
репозитория).

Маппинг колонок CSV -> поля карточки (контракт: "name, winery_name,
region_name, grapes, color, category, description"). Буквальные заголовки
дампа — "Название вина, Категория, Цвет, Регион, Сорт винограда, Описание,
Винодельня, Slug, Название фото". Маппинг ниже — ПО СОДЕРЖИМОМУ колонок, НЕ
по совпадению слова с полем (проверено на всём дампе, 6326 строк, перед
написанием скрипта):
  - CSV "Категория" фактически несёт ЦВЕТ вина — ровно 4 значения на весь
    дамп: Белое/Красное/Розовое/Оранжевое. То же понятие, что
    app/rag/fixtures.py::WINES[].color в остальном приложении. Мапится в
    поле "color", НЕСМОТРЯ на совпадение слова с колонкой "Категория".
  - CSV "Цвет" фактически несёт ОТТЕНОК В БОКАЛЕ (Светло-соломенный,
    Рубиновый, Тёмно-рубиновый...). В дампе НЕТ колонки с уровнем сахара
    (брют/сухое/сладкое — то, что в OCR-верификаторе называется "category",
    packages/cv/cv/verify.py::_CATEGORY_KEYWORDS) — за неимением лучшего
    кандидата оттенок мапится в поле "category".
  Оба поля — свободный текст на фолбэк-карточке; ни один тест/фильтр их не
  парсит программно (WineResponse.source — untyped dict, contracts/
  openapi.yaml). Решение зафиксировано явно здесь и в reports/
  b8-candidates-card.md — оркестратор может перемаппить в одну правку, если
  не согласен с выбором.
  - "Сорт винограда" -> grapes: список, сплит по запятой (как
    app/rag/fixtures.py::WINES[].grapes) — не голая строка.
Дубли слагов (2044 из 2103 уникальных на дампе — почти у каждого вина 2+
строки, по одной на файл фото импорта) схлопываются в порядке файла: ПЕРВОЕ
непустое значение по каждому полю побеждает (контракт, дословно).
case_catalog.json НЕ завязан на наличие превью — сюда попадают ВСЕ слаги
дампа независимо от usable в slug_refs.json (это отдельный слой превью, см.
build_case_thumbs.py).
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

_DEFAULT_CASE_DATA_DIR = "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"
_CSV_NAME = "strapi_output0709.csv"
_OUT_NAME = "case_catalog.json"

# поле карточки -> колонка CSV (см. докстринг модуля — маппинг по
# содержимому колонки, не по совпадению слова).
_COLUMN_BY_FIELD = {
    "name": "Название вина",
    "winery_name": "Винодельня",
    "region_name": "Регион",
    "color": "Категория",
    "category": "Цвет",
    "description": "Описание",
}
_GRAPES_COLUMN = "Сорт винограда"
_SLUG_COLUMN = "Slug"


def _case_data_dir() -> Path:
    return Path(os.environ.get("CASE_DATA_DIR", _DEFAULT_CASE_DATA_DIR))


def _parse_grapes(raw: str) -> list[str]:
    return [g.strip() for g in raw.split(",") if g.strip()]


def build_catalog(csv_path: Path) -> dict[str, dict]:
    """Читает CSV, схлопывает дубли слагов (первое непустое поле побеждает,
    в порядке строк файла), возвращает {slug: {name, winery_name,
    region_name, grapes, color, category, description}}."""
    raw_by_slug: dict[str, dict[str, str]] = {}

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            slug = (row.get(_SLUG_COLUMN) or "").strip()
            if not slug:
                continue
            entry = raw_by_slug.setdefault(slug, {})
            for field_name, column in _COLUMN_BY_FIELD.items():
                if entry.get(field_name):
                    continue  # первое непустое уже занято (контракт)
                value = (row.get(column) or "").strip()
                if value:
                    entry[field_name] = value
            if not entry.get("grapes_raw"):
                value = (row.get(_GRAPES_COLUMN) or "").strip()
                if value:
                    entry["grapes_raw"] = value

    mapping: dict[str, dict] = {}
    for slug, entry in raw_by_slug.items():
        mapping[slug] = {
            "name": entry.get("name", ""),
            "winery_name": entry.get("winery_name", ""),
            "region_name": entry.get("region_name", ""),
            "grapes": _parse_grapes(entry.get("grapes_raw", "")),
            "color": entry.get("color", ""),
            "category": entry.get("category", ""),
            "description": entry.get("description", ""),
        }
    return mapping


def main() -> None:
    case_dir = _case_data_dir()
    csv_path = case_dir / _CSV_NAME
    out_path = case_dir / _OUT_NAME
    if not csv_path.exists():
        raise SystemExit(f"CSV не найден: {csv_path}")

    mapping = build_catalog(csv_path)
    payload = {
        "generated_by": "apps/api/scripts/build_case_catalog.py",
        "source_csv": _CSV_NAME,
        "count": len(mapping),
        "mapping": mapping,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    print(f"{len(mapping)} слагов -> {out_path} ({out_path.stat().st_size} байт)")


if __name__ == "__main__":
    main()
