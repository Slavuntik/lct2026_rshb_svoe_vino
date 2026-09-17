"""Near-dup семьи каталога (`case-data/families.json`, F3) — источник для
`Match.gap` "по семье" (contracts/image-scan.md v0.4.7 п.1, agents/G4-family-gap.md).

Отдельный модуль, не `cv/index.py` — так `ImageIndex` не тащит знание о конкретной
JSON-схеме F3 внутрь своей арифметики группировки, и модуль переиспользуем (уже так
делает `scripts/near_dup_gap_report.py`, G3, независимо от этого файла).

Схема (F3, `qa/case_census.py`, 16.09.2026 — та же, что читает
`cv.cli.load_near_dup_groups`): `{family_id: {"slugs": [...], ...}}` ИЛИ обёртка
`{"families": {...}}`; значение семьи — голый список ИЛИ dict с ключом "slugs".
Отсутствующий/битый файл -> {} (не ошибка — вызывающий код тогда просто не находит
семей и остаётся на эпсилон-фолбэке, контракт: "ничего не ломая").
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cv import config


def load_family_by_slug(families_json: Path) -> dict[str, str]:
    """`slug -> family_id`, по данным `families_json`. Слаг без записи ни в одной
    семье просто отсутствует в результирующем словаре — вызывающий код (`cv/index.py::
    _gaps_to_next_family`) трактует отсутствие как "семья из одного себя", НЕ как
    ошибку."""
    if not families_json.exists():
        return {}
    try:
        payload = json.loads(families_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    families = payload.get("families", payload)  # {"families": {...}} (обёртка) или плоский словарь
    out: dict[str, str] = {}
    for family_id, value in families.items():
        slugs = value.get("slugs") if isinstance(value, dict) else value
        for slug in slugs or []:
            out[slug] = family_id
    return out


def default_families_path() -> Path:
    """`CV_FAMILIES_JSON`, иначе `$CASE_DATA_DIR/families.json` (контракт v0.4.7 п.1).

    Читает `os.environ` ЖИВЬЁМ при каждом вызове (не замораживает значение при
    импорте модуля, в отличие от большинства констант `cv.config`, см. комментарий
    в `cv/index.py::ImageIndex.__init__` про `MANIFEST_PATH`) — вызывается лениво из
    `ImageIndex._get_family_by_slug()` в момент первого `search()`, а не при импорте
    пакета, так что тестам/вызывающему коду достаточно выставить env ДО первого
    поиска, не до импорта `cv`."""
    raw = os.environ.get("CV_FAMILIES_JSON")
    if raw:
        return Path(raw)
    return config.CASE_DATA_DIR / "families.json"
