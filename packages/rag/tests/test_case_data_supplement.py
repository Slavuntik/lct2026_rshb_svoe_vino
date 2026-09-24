"""rag.case_data — дополнение "wines" из case_catalog.json каталога кейса
(reports/backend-rag-rebuild.md, 22.09): вина, которых нет в
VINES_ROOT/build/index.jsonl (снимок пайплайна 25.08), но есть в
case_catalog.json (снимок кейса-сканера, свежее и полнее). Синтетический
case_catalog.json ниже — НЕ настоящие данные (те вне git, случайно не
установлены на машине CI), та же дисциплина изоляции, что у tests/conftest.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.base import Retriever
from rag.case_data import (
    build_supplemental_wine_records,
    build_wine_record_from_case_catalog,
    load_case_catalog,
)
from rag.ingest import run_ingest
from rag.rerank import NoOpReranker


def _write_case_catalog(root: Path, mapping: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "apps/api/scripts/build_case_catalog.py",
        "source_csv": "strapi_output0709.csv",
        "count": len(mapping),
        "mapping": mapping,
    }
    path = root / "case_catalog.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


_SAMPLE_MAPPING = {
    "test-winery-pino-nuar-krasnoe-suhoe-135": {
        "name": "Пино Нуар",
        "winery_name": "Тестовая Винодельня Кейса",
        "region_name": "Кубань",
        "grapes": ["Пино Нуар"],
        "color": "Красное",
        "category": "Рубиново-красный",
        "description": "Вкус: ягодно-фруктовый, с тонами лесных ягод.",
    },
    "test-winery-shardone-beloe-suhoe-12": {
        "name": "Шардоне",
        "winery_name": "Тестовая Винодельня Кейса",
        "region_name": "Крым",
        "grapes": ["Шардоне"],
        "color": "Белое",
        "category": "Соломенный",
        "description": "Вкус: яблоко, цитрус.",
    },
}


def test_load_case_catalog_returns_mapping(tmp_path):
    _write_case_catalog(tmp_path, _SAMPLE_MAPPING)
    mapping = load_case_catalog(tmp_path)
    assert set(mapping) == set(_SAMPLE_MAPPING)


def test_load_case_catalog_missing_file_returns_empty(tmp_path):
    assert load_case_catalog(tmp_path / "no-such-dir") == {}


def test_load_case_catalog_malformed_json_returns_empty(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "case_catalog.json").write_text("{not valid json", encoding="utf-8")
    assert load_case_catalog(tmp_path) == {}


def test_build_supplemental_records_skips_known_slugs(tmp_path):
    _write_case_catalog(tmp_path, _SAMPLE_MAPPING)
    records = build_supplemental_wine_records(tmp_path, known_slugs={"test-winery-pino-nuar-krasnoe-suhoe-135"})
    assert [r.id for r in records] == ["test-winery-shardone-beloe-suhoe-12"]


def test_build_supplemental_records_empty_when_all_known(tmp_path):
    _write_case_catalog(tmp_path, _SAMPLE_MAPPING)
    records = build_supplemental_wine_records(tmp_path, known_slugs=set(_SAMPLE_MAPPING))
    assert records == []


def test_build_supplemental_records_empty_without_case_data(tmp_path):
    records = build_supplemental_wine_records(tmp_path / "no-such-dir", known_slugs=set())
    assert records == []


def test_record_shape_matches_ingest_contract():
    """payload несёт те же ключи (filters/sensory/source/derived), что
    rag.ingest.build_wine_records() — иначе rag/meta.py::public_meta и
    rag/ingest.py::run_ingest() (filt["stillness"] и т.п.) сломались бы."""
    entry = _SAMPLE_MAPPING["test-winery-pino-nuar-krasnoe-suhoe-135"]
    rec = build_wine_record_from_case_catalog("test-winery-pino-nuar-krasnoe-suhoe-135", entry)

    assert rec.id == "test-winery-pino-nuar-krasnoe-suhoe-135"
    assert rec.kind == "wine"
    assert rec.url == "https://vino-svoe.ru/wines/test-winery-pino-nuar-krasnoe-suhoe-135"
    assert set(rec.payload) == {"filters", "sensory", "source", "derived"}

    source = rec.payload["source"]
    assert source["name"] == "Пино Нуар"
    assert source["winery_name"] == "Тестовая Винодельня Кейса"
    assert source["region_name"] == "Кубань"
    assert source["grapes"] == ["Пино Нуар"]  # список, не None — фронт зовёт .join()
    assert source["color"] == "красное"  # lowercase — тот же регистр, что у vines-карточек
    assert source["color_in_glass"] == "Рубиново-красный"
    assert source["sugar_category"] is None  # честно: в case_catalog.json нет уровня сахара

    filt = rec.payload["filters"]
    assert filt["color"] == "красное"
    assert filt["sugar"] is None
    assert filt["region"] == "kuban"  # refdata.normalize_region("Кубань")

    derived = rec.payload["derived"]
    assert derived["sensory"] == {}
    assert derived["reference_style_matches"] == []

    # текст несёт все известные поля — материал для dense/BM25
    assert "Пино Нуар" in rec.text
    assert "Тестовая Винодельня Кейса" in rec.text
    assert "Кубань" in rec.text


def test_record_grapes_resolved_to_slugs_via_refdata():
    entry = {
        "name": "Каберне Совиньон", "winery_name": "Тест", "region_name": "",
        "grapes": ["Каберне Совиньон"], "color": "Красное", "category": "", "description": "",
    }
    rec = build_wine_record_from_case_catalog("test-cabernet", entry)
    assert "kaberne-sovinon" in rec.payload["filters"]["grapes"]
    assert "kaberne-sovinon" in rec.payload["derived"]["grape_slugs"]


def test_record_handles_missing_optional_fields_gracefully():
    """Пустая строка/отсутствующее поле — не пустая ошибка: source.grapes
    остаётся списком (даже пустым), region/winery slug — None, не падаем."""
    rec = build_wine_record_from_case_catalog(
        "test-bare-slug", {"name": "", "winery_name": "", "region_name": "", "grapes": [], "color": "", "category": "", "description": ""}
    )
    assert rec.payload["source"]["name"] == "test-bare-slug"  # честный fallback на slug
    assert rec.payload["source"]["grapes"] == []
    assert rec.payload["source"]["region"] is None
    assert rec.payload["filters"]["region"] is None
    assert rec.text  # текст не пуст даже без полей — хотя бы slug попадёт в name


@pytest.mark.parametrize(
    "winery_name,expected_contains",
    [
        ("Абрау-Дюрсо", "abrau"),
        ("Шато АЛВИСА", "shato"),
    ],
)
def test_winery_slugify_transliterates_cyrillic(winery_name, expected_contains):
    entry = {
        "name": "Тест", "winery_name": winery_name, "region_name": "",
        "grapes": [], "color": "", "category": "", "description": "",
    }
    rec = build_wine_record_from_case_catalog("test-slug", entry)
    winery_slug = rec.payload["source"]["winery"]
    assert winery_slug is not None
    assert expected_contains in winery_slug
    assert winery_slug == winery_slug.lower()
    assert " " not in winery_slug


# --------------------------------------------------------------------------
# Интеграция с run_ingest(): дополнение реально попадает в собранный индекс,
# не только в промежуточный SourceRecord.
# --------------------------------------------------------------------------


def test_run_ingest_merges_case_data_supplement_into_wines_collection(tiny_source, tmp_path):
    build_dir, catalog_dir = tiny_source  # 7 вин, см. tests/conftest.py — НЕ мутируем, только читаем
    case_data_root = tmp_path / "case-data"
    _write_case_catalog(
        case_data_root,
        {
            "case-only-extra-wine-1": {
                "name": "Кейс-Экстра Резерв",
                "winery_name": "Кейсовая Винодельня",
                "region_name": "Крым",
                "grapes": ["Рислинг"],
                "color": "Белое",
                "category": "Соломенный",
                "description": "Вкус: минеральность, цитрус — вино только из case_catalog.json.",
            }
        },
    )
    data_dir = tmp_path / "data"
    no_goldset = tmp_path / "no-such-goldset.jsonl"

    manifest = run_ingest(
        version="test-supplement",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=case_data_root,
    )

    assert manifest["counts"]["wines"] == 8  # 7 фикстуры + 1 из case_catalog.json
    assert manifest["case_data_supplement"]["added"] == 1
    assert manifest["case_data_supplement"]["added_slugs"] == ["case-only-extra-wine-1"]

    retriever = Retriever(data_dir=data_dir, reranker=NoOpReranker())
    candidate = retriever.get_by_id("case-only-extra-wine-1")
    assert candidate is not None
    assert candidate.kind == "wine"
    assert candidate.meta["source"]["name"] == "Кейс-Экстра Резерв"
    assert candidate.meta["source"]["winery_name"] == "Кейсовая Винодельня"

    # найдётся и через обычный search() — тем же вопросом, что шлёт кнопка
    # "Спросить сомелье об этом вине" (apps/web/src/i18n/ru.ts:156), не
    # только по прямому id.
    results = retriever.search("Расскажи про Кейс-Экстра Резерв от Кейсовая Винодельня", top_k=8)
    assert "case-only-extra-wine-1" in [c.id for c in results]


def test_run_ingest_without_case_data_dir_argument_is_unaffected_when_pointed_at_empty_dir(tiny_source, tmp_path):
    """case_data_dir явно передан, но пуст (нет case_catalog.json) — ровно 7
    вин фикстуры, дополнение — честный no-op (не ошибка)."""
    build_dir, catalog_dir = tiny_source
    data_dir = tmp_path / "data"
    no_goldset = tmp_path / "no-such-goldset.jsonl"

    manifest = run_ingest(
        version="test-empty-case-data",
        source_dir=build_dir,
        catalog_dir=catalog_dir,
        data_dir=data_dir,
        goldset_path=no_goldset,
        case_data_dir=tmp_path / "empty-case-data",
    )

    assert manifest["counts"]["wines"] == 7
    assert manifest["case_data_supplement"]["added"] == 0
