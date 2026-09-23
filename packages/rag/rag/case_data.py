"""Дополнение коллекции "wines" из каталога кейса-сканера (case-data,
`CASE_DATA_DIR/case_catalog.json` — apps/api/scripts/build_case_catalog.py,
дамп `strapi_output0709.csv`), read-only, вне git.

Почему это отдельный модуль (reports/backend-rag-rebuild.md, 22.09): индекс
собирался из `VINES_ROOT/build/index.jsonl` (снимок пайплайна vines 25.08,
1978 вин) — тимлид зафиксировал регресс: каталог кейса-сканера на 22.09 несёт
2103 вина, 125 отсутствуют в `index.jsonl` целиком (не просто без карточки —
их slug'а там физически нет), хотя описание у них есть в `case_catalog.json`.
Симптом с проды: скан находит вино (у CV своя, независимая деградация через
`app/rag/case_catalog.py::lookup()`), кнопка "Спросить сомелье" строит
осмысленный вопрос (карточка резолвится тем же фолбэком), но
`Retriever.search()` этот slug никогда не вернёт — его нет НИ в Qdrant, НИ в
BM25, ни в одном сайдкаре.

`case_catalog.json` СИЛЬНО беднее `index.jsonl`: только name/winery_name/
region_name/grapes/color(=цвет, 4 значения)/category(=оттенок в бокале, НЕ
сахар — в CSV нет колонки уровня сахара вовсе, см. build_case_catalog.py)/
description. Нет sensory, food_pairings, rating, abv, stillness,
similar_wine_slugs — эти поля честно остаются пустыми/null, а не
довыдумываются: то же правило "ничего не досочинять", что у ref/grape_synonyms.yaml.
Даунстрим (rag/meta.py::public_meta, app/food_pairing.py::compute_pairings,
WineCardContent.tsx) уже умеет деградировать на пустых derived/пустом
food_pairings — тот же уровень сервиса, что у СУЩЕСТВУЮЩЕГО фолбэка
`app/rag/case_catalog.py` (basis="heuristic" в пейрингах, карточка без
"похожих вин") — это НЕ регресс, просто те же 125 вин теперь ЕЩЁ и попадают в
retrieval, не только в карточку по прямому id.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from rag import refdata
from rag.types import SourceRecord

# case_catalog.json несёт эти значения дословно с портала (build_case_catalog.py,
# _COLUMN_BY_FIELD["color"] = CSV "Категория") — 4 варианта, регистр как на
# портале ("Белое"/"Красное"/...). Приводим к нижнему регистру — во ВСЁМ
# остальном каталоге (vines index.jsonl/catalog card) color лежит лоуэркейсом
# ("красное", не "Красное"; app/food_pairing.py::build_heuristic_wine_vector
# и rag/filtering.py::passes_filters сравнивают по значению, а не .lower()).
_SOURCE_URL_TMPL = "https://vino-svoe.ru/wines/{slug}"
_THUMB_URL_TMPL = "/v1/case-thumbs/{slug}.webp"


def load_case_catalog(case_data_dir: Path) -> dict[str, dict]:
    """`{slug: {name, winery_name, region_name, grapes, color, category,
    description}}` из `<case_data_dir>/case_catalog.json`. Файл может
    отсутствовать (машина без case-data) — честно `{}`, тот же принцип
    graceful degradation, что `app/rag/case_catalog.py::_load_catalog`."""
    path = case_data_dir / "case_catalog.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    mapping = payload.get("mapping") if isinstance(payload, dict) else None
    return mapping if isinstance(mapping, dict) else {}


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}


def _slugify_winery(name: str) -> str | None:
    """Грубый транслит+slug для `filters.winery`/`source.winery` — НЕ ключ:
    ни жёсткая фильтрация (rag/store.py::build_filter, rag/filtering.py —
    оба обходятся без filters.winery), ни резолв не зависят от точного
    совпадения с реальным slug'ом винодельни vines (labels.jsonl строится по
    winery_NAME, см. rag/ingest.py::build_labels). Только косметика карточки/
    отладки — не стоит точной транслитерации."""
    if not name:
        return None
    transliterated = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-")
    return slug or None


def build_wine_record_from_case_catalog(slug: str, entry: dict) -> SourceRecord:
    """Один SourceRecord из строки `case_catalog.json` — форма payload
    ЗЕРКАЛИТ то, что `rag.ingest.build_wine_records()` собирает из
    index.jsonl+catalog card (те же ключи filters/sensory/source/derived), с
    честно пустыми полями там, где у case_catalog.json нет данных."""
    name = (entry.get("name") or "").strip() or slug
    winery_name = (entry.get("winery_name") or "").strip()
    region_name = (entry.get("region_name") or "").strip()
    grapes = [g.strip() for g in (entry.get("grapes") or []) if isinstance(g, str) and g.strip()]
    color = (entry.get("color") or "").strip().lower() or None
    color_in_glass = (entry.get("category") or "").strip() or None  # см. докстринг модуля — НЕ сахар
    description = (entry.get("description") or "").strip()

    region_slug = refdata.normalize_region(region_name) if region_name else None
    grape_name_to_slug = refdata.grape_name_to_slug()
    grape_slugs = sorted({grape_name_to_slug[g] for g in grapes if g in grape_name_to_slug})
    winery_slug = _slugify_winery(winery_name)

    text = " · ".join(
        p for p in (name, winery_name, region_name, ", ".join(grapes), color, color_in_glass, description) if p
    )
    url = _SOURCE_URL_TMPL.format(slug=slug)

    filt = {
        "color": color,
        "sugar": None,  # честно: в CSV нет колонки уровня сахара (см. докстринг)
        "region": region_slug,
        "winery": winery_slug,
        "grapes": grape_slugs,
        "food": [],
        "rating": None,
        "abv": None,
        "stillness": None,
        "reference_style_matches": [],
    }
    source = {
        "name": name,
        "winery": winery_slug,
        "winery_name": winery_name,
        "region": region_slug,
        "region_name": region_name,
        "grapes": grapes,  # список, НЕ None — WineCardContent.tsx зовёт .join() без guard
        "color": color,
        "sugar_category": None,
        "color_in_glass": color_in_glass,
        "vintage": None,
        "abv_percent": None,
        "serving_temp_c": None,
        "food_pairings": [],
        "description": description,
        "public_rating": None,
        "public_rating_count": None,
        "roskachestvo_rating": None,
        "image_url": _THUMB_URL_TMPL.format(slug=slug),
        "similar_wine_slugs": [],
    }
    derived = {
        "stillness": None,
        "style_tags": [],
        "grape_slugs": grape_slugs,
        "aroma_descriptors": [],
        "sensory": {},
        "price_tier": None,
        "reference_style_matches": [],
    }
    payload = {"filters": filt, "sensory": {}, "source": source, "derived": derived}
    return SourceRecord(id=slug, kind="wine", text=text, url=url, payload=payload)


def build_supplemental_wine_records(case_data_dir: Path, known_slugs: set[str]) -> list[SourceRecord]:
    """Вина `case_catalog.json`, которых нет в `known_slugs` (обычно —
    id'ы, уже построенные `build_wine_records()` из index.jsonl). Порядок —
    как в `case_catalog.json` (детерминирован: build_case_catalog.py пишет
    `sort_keys=True`)."""
    mapping = load_case_catalog(case_data_dir)
    return [
        build_wine_record_from_case_catalog(slug, entry)
        for slug, entry in mapping.items()
        if slug not in known_slugs
    ]
