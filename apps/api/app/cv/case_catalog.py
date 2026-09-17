"""Метаданные кандидатов верификатора из КАТАЛОГА КЕЙСА, не из нашего RAG
(contracts/image-scan.md, "Дополнения v0.4.9" после живого e2e B5,
reports/b5-gate-v048.md §2, "Причина 3").

Диагноз B5 (живой e2e, q2 "Мускатель Массандра", IMAGE_PROVIDER=real
VERIFIER_PROVIDER=real RAG_PROVIDER=mock): даже когда кандидат корректно
попадает в `candidate_slugs` (v0.4.8, близость score), `_verify_candidates()`
брал name/vintage через `retriever.get_by_id()` — путь к НАШЕМУ RAG-каталогу.
Два независимых пробела разом:
  1. Кейс-слаги (case-data/slug_refs.json) в нашем wines-каталоге отсутствуют
     ВООБЩЕ — get_by_id возвращает None, честная деградация даёt name=slug
     (латиница/транслит: "massandra-muskat-belyy-...").
  2. Даже при RAG_PROVIDER=mock, что штатно для dev/тестового профиля, мок
     не знает кейс-каталог вовсе — тот же результат.
В обоих случаях verify.py::_WINE_TYPE_KEYWORDS/_COLOR_KEYWORDS (кириллица,
граница слова) никогда не совпадут с латинским slug'ом — верификатор либо не
находит НИ ОДНОГО сигнала (matched_on: []), либо совпадает случайно. Источник
этого модуля — ПРАВИЛЬНЫЕ кириллические name/winery из дампа кейса, доказано
B5 изолированной проверкой match_candidates() на кириллице (сработала верно).

Формат `case-data/slug_refs.json` (генератор `qa/case_census.py`; тот же файл
кейса, другая проекция от `packages/cv/cv/families.py`):
    {"mapping": {slug: {"name": "...", "winery": "...", ...}}, ...}
Файла может не быть (case-data не приехали на этой машине / другой профиль)
— тогда `_load_mapping()` лениво резолвится в `{}`, и `lookup()` честно
отдаёт `None` на любой slug; вызывающий код (`app/cv/service.py::
_verify_candidates`) обязан откатиться на прежний путь `retriever.get_by_id()`
— ничего не ломая, тот же принцип деградации, что и
`cv.families.load_family_by_slug()` в packages/cv.

Ленивая загрузка, один раз (контракт: "ленивая загрузка при старте, один
раз"): файл НЕ читается при импорте модуля — `_slug_refs_path()` читает env
`CASE_DATA_DIR` живьём при каждом вызове (тот же паттерн, что
`cv.families.default_families_path()`), так что тесты успевают
`monkeypatch.setenv("CASE_DATA_DIR", ...)` до первого обращения, не до
импорта. Кэш `_load_mapping()` ключуется САМИМ ПУТЁМ (`@lru_cache`), не
глобальным флагом — на боевой машине путь стабилен (env не меняется в
процессе), файл читается с диска ровно один раз за жизнь процесса; тесты,
подменяющие `CASE_DATA_DIR` на tmp-фикстуру с ДРУГИМ путём, получают честный
cache miss и не видят чужой (прод- или чужого теста) кэш.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

# Тот же формат года, что OCR-верификатор (packages/cv/cv/verify.py::_YEAR_RE,
# "ровно 4 цифры вида 19xx/20xx, не как часть более длинного числа") —
# отдельная копия, не импорт: apps/api не тянет packages/cv как обязательную
# зависимость в мок-режиме (см. app/cv/interface.py — Protocol'ы дублируются
# по тому же принципу, "сверено посимвольно", не общий импорт между пакетами).
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

_DEFAULT_CASE_DATA_DIR = "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"


class CaseMetadata(NamedTuple):
    name: str
    vintage: int | None


def _slug_refs_path() -> Path:
    """`$CASE_DATA_DIR/slug_refs.json` — env читается ЖИВЬЁМ при каждом
    вызове, НЕ замораживается при импорте модуля (см. докстринг файла)."""
    case_data_dir = os.environ.get("CASE_DATA_DIR", _DEFAULT_CASE_DATA_DIR)
    return Path(case_data_dir) / "slug_refs.json"


@lru_cache(maxsize=None)
def _load_mapping(path_str: str) -> dict[str, dict]:
    """`{slug: {"name", "winery", ...}}` из `slug_refs.json` по пути
    `path_str` — кэш ключом по пути (см. докстринг модуля). Отсутствующий/
    битый файл или неожиданная форма JSON -> `{}` (не ошибка — тот же
    принцип честной деградации, что `cv.families.load_family_by_slug`)."""
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = payload.get("mapping") if isinstance(payload, dict) else None
    return mapping if isinstance(mapping, dict) else {}


def _extract_vintage(*sources: str | None) -> int | None:
    """Год 19xx/20xx — из первого источника (по порядку аргументов), где он
    нашёлся; контракт: "vintage — год из слага/имени, если есть". Большинство
    кейс-слагов явного винтажа в дампе не несут вовсе — тогда `None`, как и
    раньше (get_by_id-фолбэк уже умел отдавать vintage=None честно)."""
    for src in sources:
        if not src:
            continue
        match = _YEAR_RE.search(src)
        if match:
            return int(match.group())
    return None


def lookup(slug: str) -> CaseMetadata | None:
    """(name, vintage) из каталога КЕЙСА для `slug` — `None`, если
    `slug_refs.json` недоступен ИЛИ не знает этот slug (вызывающий код тогда
    обязан откатиться на `retriever.get_by_id()`, см.
    `app/cv/service.py::_verify_candidates`).

    `name` — `"{winery} {name}"` каталога кейса (контракт: "name (и
    winery)"), не голое `.name`: OCR-верификатор ищет токены тип/цвет/год по
    ВСЕЙ строке `VerifyCandidate["name"]` (`packages/cv/cv/verify.py::
    _tokens`, сопоставление — точный regex по границе слова, не фаззи-
    подстрока) — добавление winery безопасно расширяет текст без ложных
    срабатываний (ни одно из 135 названий виноделен каталога кейса не
    содержит слово-омоним ключевых токенов _WINE_TYPE_KEYWORDS/
    _COLOR_KEYWORDS/_CATEGORY_KEYWORDS, проверено при разработке)."""
    mapping = _load_mapping(str(_slug_refs_path()))
    entry = mapping.get(slug)
    if entry is None:
        return None
    name = (entry.get("name") or slug).strip()
    winery = (entry.get("winery") or "").strip()
    full_name = f"{winery} {name}".strip() if winery else name
    vintage = _extract_vintage(slug, full_name)
    return CaseMetadata(name=full_name, vintage=vintage)
