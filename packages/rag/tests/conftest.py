"""Общие фикстуры: маленький синтетический каталог (НЕ настоящие данные vines)
для быстрых, детерминированных, изолированных тестов. Настоящие vines-данные
read-only и не годятся для юнит-тестов (медленно, зависит от live-каталога).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag.base import Retriever
from rag.ingest import run_ingest
from rag.rerank import NoOpReranker

# ------------------------------------------------------------------------- #
# Синтетические вина: явно разведены по цвету/сахару/региону/сорту, чтобы
# тесты на фильтры и релевантность были однозначными.
# ------------------------------------------------------------------------- #
_WINES = [
    dict(
        id="red-dry-kuban-1",
        name="Тестовый Каберне Фран",
        winery="test-winery-a",
        winery_name="Тестовая Винодельня А",
        region="kuban",
        color="красное",
        sugar="сухое",
        grapes=["kaberne-fran"],
        food=["Мясо и стейки"],
        text="Тестовый Каберне Фран · красное · сухое · Кубань · Мясо и стейки. Вкус: чёрная смородина, перец.",
        stillness="тихое",
        styles=[],
    ),
    dict(
        id="red-dry-kuban-2",
        name="Тестовый Мерло Резерв",
        winery="test-winery-a",
        winery_name="Тестовая Винодельня А",
        region="kuban",
        color="красное",
        sugar="сухое",
        grapes=["merlo"],
        food=["Мясо и стейки", "Сыры"],
        text="Тестовый Мерло Резерв · красное · сухое · Кубань · Мясо и стейки, Сыры. Вкус: слива, ваниль.",
        stillness="тихое",
        styles=[],
    ),
    dict(
        id="white-dry-fish-1",
        name="Тестовый Совиньон Блан",
        winery="test-winery-b",
        winery_name="Тестовая Винодельня Б",
        region="krym",
        color="белое",
        sugar="сухое",
        grapes=["sovinon-blan"],
        food=["Рыба и морепродукты", "Устрицы"],
        text="Тестовый Совиньон Блан · белое · сухое · Крым · Рыба и морепродукты, Устрицы. "
        "Вкус: цитрус, свежесть, отлично к рыбе и морепродуктам.",
        stillness="тихое",
        styles=[],
    ),
    dict(
        id="white-dry-fish-2",
        name="Тестовое Шардоне Крю",
        winery="test-winery-b",
        winery_name="Тестовая Винодельня Б",
        region="krym",
        color="белое",
        sugar="сухое",
        grapes=["shardone"],
        food=["Рыба и морепродукты"],
        text="Тестовое Шардоне Крю · белое · сухое · Крым · Рыба и морепродукты. "
        "Вкус: яблоко, минеральность, подходит к рыбе.",
        stillness="тихое",
        styles=[],
    ),
    dict(
        id="rose-dry-1",
        name="Тестовое Розе",
        winery="test-winery-a",
        winery_name="Тестовая Винодельня А",
        region="kuban",
        color="розовое",
        sugar="сухое",
        grapes=["kaberne-fran"],
        food=["Салаты"],
        text="Тестовое Розе · розовое · сухое · Кубань · Салаты. Вкус: клубника, лёгкость.",
        stillness="тихое",
        styles=[],
    ),
    dict(
        id="sparkling-white-prosecco-1",
        name="Тестовый Игристый Глера",
        winery="test-winery-b",
        winery_name="Тестовая Винодельня Б",
        region="krym",
        color="белое",
        sugar="экстра брют",
        grapes=["glera"],
        food=["Легкие закуски"],
        text="Тестовый Игристый Глера · белое · экстра брют · Крым · игристое. Вкус: яблоко, свежесть, пузырьки.",
        stillness="игристое",
        styles=["prosecco"],
    ),
    dict(
        id="sparkling-red-decoy-1",
        name="Тестовый Красный Игристый",
        winery="test-winery-a",
        winery_name="Тестовая Винодельня А",
        region="kuban",
        color="красное",
        sugar="полусладкое",
        grapes=["shardone"],
        food=["Десерты"],
        text="Тестовый Красный Игристый · красное · полусладкое · Кубань · игристое. Вкус: ягоды, сладость.",
        stillness="игристое",
        styles=["sparkling-shiraz"],
    ),
]

_WINERY = dict(
    id="test-winery-a",
    text="Тестовая Винодельня А · Кубань · небольшое семейное хозяйство для тестов ingest.",
    url="https://example.invalid/wineries/test-winery-a",
    region="Кубань",
)

_ARTICLE_CHUNKS = [
    dict(
        id="test-article-fact#0",
        article_id="test-article-fact",
        title="Тестовая статья о танинах",
        text="Танины — вещества из кожицы винограда, ощущаются как терпкость. Это тестовый факт про танины.",
        url="https://example.invalid/articles/test-article-fact",
        rubric="Гид",
    ),
]


def _write_source(root: Path) -> tuple[Path, Path]:
    build_dir = root / "build"
    catalog_dir = root / "catalog"
    (build_dir).mkdir(parents=True, exist_ok=True)
    (catalog_dir / "wines").mkdir(parents=True, exist_ok=True)
    (catalog_dir / "wineries").mkdir(parents=True, exist_ok=True)

    with open(build_dir / "index.jsonl", "w", encoding="utf-8") as f:
        for w in _WINES:
            row = {
                "id": w["id"],
                "text": w["text"],
                "url": f"https://example.invalid/wines/{w['id']}",
                "filters": {
                    "color": w["color"],
                    "sugar": w["sugar"],
                    "region": w["region"],
                    "winery": w["winery"],
                    "grapes": w["grapes"],
                    "food": w["food"],
                    "rating": None,
                    "abv": 12.5,
                },
                "sensory": {"sweetness": 0.1, "acidity": 0.6, "tannin": 0.3, "body": 0.5, "bubbles": 0.0},
                "styles": w["styles"],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

            card = {
                "slug": w["id"],
                "source": {
                    "name": w["name"],
                    "winery": w["winery"],
                    "winery_name": w["winery_name"],
                    "region": w["region"],
                    "region_name": w["region"],
                    "grapes": w["grapes"],
                    "color": w["color"],
                    "sugar_category": w["sugar"],
                    "vintage": None,
                    "abv_percent": 12.5,
                    "serving_temp_c": [10, 12],
                    "food_pairings": w["food"],
                    "description": w["text"],
                    "public_rating": None,
                    "roskachestvo_rating": None,
                    "image_url": None,
                    "similar_wine_slugs": [],
                },
                "derived": {
                    "stillness": w["stillness"],
                    "reference_style_matches": w["styles"],
                    "sensory": {"sweetness": 0.1, "acidity": 0.6},
                },
            }
            with open(catalog_dir / "wines" / f"{w['id']}.json", "w", encoding="utf-8") as cf:
                json.dump(card, cf, ensure_ascii=False)

    with open(build_dir / "wineries.jsonl", "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": _WINERY["id"],
                    "text": _WINERY["text"],
                    "url": _WINERY["url"],
                    "filters": {"region": _WINERY["region"], "vineyard_area_ha": 1.0, "founded_year": 2010, "wines": 2},
                    "wine_slugs": ["red-dry-kuban-1", "red-dry-kuban-2"],
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    with open(catalog_dir / "wineries" / f"{_WINERY['id']}.json", "w", encoding="utf-8") as f:
        json.dump({"slug": _WINERY["id"], "source": {"name": "Тестовая Винодельня А"}}, f, ensure_ascii=False)

    with open(build_dir / "articles.jsonl", "w", encoding="utf-8") as f:
        for c in _ARTICLE_CHUNKS:
            f.write(
                json.dumps(
                    {
                        "id": c["id"],
                        "type": "article",
                        "article_id": c["article_id"],
                        "title": c["title"],
                        "heading": None,
                        "text": c["text"],
                        "url": c["url"],
                        "filters": {"rubric": c["rubric"], "author": "Тест", "published_date": "2026-01-01", "year": 2026},
                        "wine_slugs": [],
                        "winery_slugs": [],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    return build_dir, catalog_dir


@pytest.fixture(scope="module")
def tiny_source(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("vines_src")
    return _write_source(root)


@pytest.fixture(scope="module")
def tiny_index(tmp_path_factory, tiny_source) -> Retriever:
    """Полный ingest мини-каталога в изолированный data_dir + Retriever без
    реранкера (NoOpReranker) — тесты на фильтры/идемпотентность/eval не должны
    зависеть от кросс-энкодера и его кэша на конкретной машине.

    goldset_path указывает на заведомо отсутствующий файл: калибровка
    refusal-порога (rag/ingest.py) иначе по умолчанию взяла бы РЕАЛЬНЫЙ
    eval/goldset.jsonl (75 вопросов) и прогнала бы его через реальный
    кросс-энкодер поверх мини-каталога — бессмысленно (вопросы про реальные
    вина, которых в фикстуре нет) и на порядок медленнее. refusal_threshold
    для этой фикстуры остаётся None (выключен) — он отдельно юнит-тестируется
    в test_refusal.py на фиктивном реранкере, без потребности в калибровке."""
    build_dir, catalog_dir = tiny_source
    data_dir = tmp_path_factory.mktemp("rag_data")
    no_goldset = data_dir / "no-such-goldset.jsonl"
    run_ingest(
        version="test", source_dir=build_dir, catalog_dir=catalog_dir, data_dir=data_dir, goldset_path=no_goldset
    )
    return Retriever(data_dir=data_dir, reranker=NoOpReranker())
