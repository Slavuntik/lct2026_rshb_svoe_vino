"""Фолбэк-метаданные вина из каталога кейса — когда `wine_id` не резолвится
нашим RAG-каталогом (contracts/image-scan.md v0.4.11, п.2: "слага нет в нашем
каталоге (RAG) -> card строится из каталога кейса"). Источник —
`CASE_DATA_DIR/case_catalog.json`, собирается офлайн-скриптом
`apps/api/scripts/build_case_catalog.py` из дампа `strapi_output0709.csv`
(агент B8, agents/B8-candidates-card.md) — 122 из 2054 usable-слагов кейса
(на снимке 21.09) отсутствуют в нашем RAG-каталоге вовсе, а приватная
проверка организаторов содержит ТОЛЬКО вина из каталога кейса.

НЕ путать с `app/cv/case_catalog.py` — тот читает ДРУГОЙ файл того же
каталога (`slug_refs.json`) для другой задачи (метаданные name/vintage для
OCR-верификатора, agents/B6). Оба модуля названы одинаково по тому же
принципу, что `app/rag/interface.py` и `app/cv/interface.py` — параллельные
модули в разных подпакетах для разных забот. В отличие от той пары (зеркала
ВНЕШНИХ пакетов, сверяемые посимвольно), общий импорт между этими двумя не
запрещён технически — просто не переиспользуется намеренно, чтобы каждый
модуль оставался читаемым в изоляции и не тянул за собой чужую заботу.

Тот же принцип ленивой загрузки, что и у `app/cv/case_catalog.py` (см. его
докстринг подробно): путь читается из env ЖИВЬЁМ при каждом вызове (не
замораживается при импорте модуля) — тесты успевают
`monkeypatch.setenv("CASE_DATA_DIR", ...)` до первого обращения. Кэш
`_load_catalog()` ключуется самим путём (`@lru_cache`), не глобальным флагом
— разные `CASE_DATA_DIR` в разных тестах не видят чужой кэш.

Файла может не быть вовсе (case-data не приехали на этой машине, или другой
профиль) — тогда `lookup()` честно отдаёт `None` на любой slug, и
`build_wine_card()` (`app/rag/cards.py`) возвращает `None`, как и раньше
(404 у GET /wines/{id}, `card=None` у /scan/photo) — эта правка НИЧЕГО не
отбирает у прежнего поведения, только добавляет источник ПОСЛЕ того, как наш
RAG уже честно не нашёл вино.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

_DEFAULT_CASE_DATA_DIR = "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"


class CaseWine(NamedTuple):
    name: str
    winery_name: str
    region_name: str
    grapes: list[str]
    color: str
    category: str
    description: str


def case_data_dir() -> Path:
    """`$CASE_DATA_DIR` — читается живьём (см. докстринг модуля). Переиспользуется
    `app/routers/case_thumbs.py` для того же корня (`thumbs/` — сосед
    `case_catalog.json` внутри одного каталога кейса) вместо третьей копии
    дефолтного пути."""
    return Path(os.environ.get("CASE_DATA_DIR", _DEFAULT_CASE_DATA_DIR))


def _case_catalog_path() -> Path:
    return case_data_dir() / "case_catalog.json"


@lru_cache(maxsize=None)
def _load_catalog(path_str: str) -> dict[str, dict]:
    """`{slug: {...}}` из `case_catalog.json` по пути `path_str` — кэш ключом
    по пути (см. докстринг модуля). Отсутствующий/битый файл или неожиданная
    форма JSON -> `{}` (честная деградация, тот же принцип, что
    `app/cv/case_catalog.py::_load_mapping`)."""
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = payload.get("mapping") if isinstance(payload, dict) else None
    return mapping if isinstance(mapping, dict) else {}


def lookup(slug: str) -> CaseWine | None:
    """Метаданные карточки кейс-слага — `None`, если `case_catalog.json`
    недоступен ИЛИ не знает этот slug (вызывающий код тогда честно
    деградирует — см. `app/rag/cards.py::build_wine_card` и
    `app/cv/service.py::_candidate_item`)."""
    mapping = _load_catalog(str(_case_catalog_path()))
    entry = mapping.get(slug)
    if entry is None:
        return None
    return CaseWine(
        name=(entry.get("name") or "").strip() or slug,
        winery_name=(entry.get("winery_name") or "").strip(),
        region_name=(entry.get("region_name") or "").strip(),
        grapes=list(entry.get("grapes") or []),
        color=(entry.get("color") or "").strip(),
        category=(entry.get("category") or "").strip(),
        description=(entry.get("description") or "").strip(),
    )


def all_slugs() -> list[str]:
    """Все slug'и, известные `case_catalog.json` — корпус для IDF текстового
    переранжирования (contracts/image-scan.md v0.4.12, agents/B9-text-rerank-
    integration.md: "тексты кандидатов — каталог кейса"; `app/cv/service.py`
    строит `cv.text_rerank.CatalogText`/IDF по каждому slug'у через `lookup()`
    ниже). Пустой список, если `case_catalog.json` недоступен — та же честная
    деградация, что и у `lookup()` (вызывающий код тогда получает пустой IDF-
    словарь, `text_score()` возвращает 0.0 для всех — переранжирование
    становится no-op, не падает)."""
    return list(_load_catalog(str(_case_catalog_path())).keys())


def source_url(slug: str) -> str:
    """contracts/image-scan.md v0.4.11 п.3: "source_url слага кейса —
    https://vino-svoe.ru/wines/<slug>" — независимо от того, знает ли
    case_catalog.json этот конкретный slug (URL строится из самого slug'а)."""
    return f"https://vino-svoe.ru/wines/{slug}"


def thumb_url(slug: str) -> str:
    """contracts/image-scan.md v0.4.11 п.4: превью фолбэка — `GET
    /v1/case-thumbs/{slug}.webp` (`app/routers/case_thumbs.py`, раздаёт из
    `CASE_DATA_DIR/thumbs/`). Маршрут сам решает 404, если превью нет —
    здесь просто формируем ссылку."""
    return f"/v1/case-thumbs/{slug}.webp"
