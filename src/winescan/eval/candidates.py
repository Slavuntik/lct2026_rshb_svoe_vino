"""Таблица признаков кандидатов по кэшу запросов — для обучения слияния и порогов отказа.

Запуск: ``python -m winescan.eval.candidates --cache synth_v2 [--top 5] [--limit N] [--workers 16]``

Для каждого запроса берётся рамка (правило из offline.box_selection или первая по весу),
визуальный top-K из кэша и для каждого кандидата — признаки проверки (search.verify) против
его эталона. Результат: artifacts/cache/<cache>/candidates_top<K>.parquet, строка на пару
«запрос × кандидат» с меткой «это верное вино». Негативы «вина нет в каталоге» получаются из
тех же строк без верного кандидата (leave-one-out) — отдельно считать не нужно.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from winescan.config import get_paths
from winescan.eval.offline import QueryCache, choose_slot
from winescan.eval.run import _load_query, load_split
from winescan.eval.train_box_ranker import query_rows
from winescan.logging_setup import setup_logging
from winescan.search.box_ranker import BoxRanker
from winescan.eval.vlm_cache import load_fields
from winescan.search.fields import LabelFields, field_score
from winescan.search.fusion import candidate_features
from winescan.search.local_features import STORE_NAME, LocalFeatureStore, load_reference_view, reference_view
from winescan.search.local_match import extract, prepare
from winescan.search.verify import verify
from winescan.service.pipeline import load_cards
from winescan.vision.preprocess import crop_box

log = logging.getLogger("winescan.eval.candidates")

_state: dict = {}


def _init(images_dir: str, image_of: dict[str, str]) -> None:
    paths = get_paths()
    _state.update(images_dir=images_dir, image_of=image_of, uploads=paths.uploads_dir,
                  store=LocalFeatureStore(paths.artifacts_dir / "index" / STORE_NAME))  # fmt: skip


@lru_cache(maxsize=2048)
def _reference(slug: str):
    view = load_reference_view(slug) or reference_view(_state["uploads"] / _state["image_of"][slug])
    return view, _state["store"].get(slug)


def _task(task: dict) -> list[dict]:
    image, scale = _load_query(os.path.join(_state["images_dir"], task["image_path"]))
    box = task["box"]
    query_image = prepare(crop_box(image, box) if box else image)
    query_features = extract(query_image)
    rows = []
    best_visual = task["candidates"][0][1]
    for rank, (slug, visual) in enumerate(task["candidates"], start=1):
        reference_image, reference_features = _reference(slug)
        verification = verify(query_image, query_features, reference_image, reference_features).as_dict()
        rows.append({"query_id": task["query_id"], "slug": slug, "rank": rank, "label": slug == task["expected"],
                     **candidate_features(visual, best_visual, verification), "inliers": verification["inliers"]})  # fmt: skip
    return rows


def _deep_rows(tasks: list[dict], images_dir: str, image_of: dict[str, str]) -> list[dict]:
    """То же, что _task, но ALIKED + LightGlue на GPU: один процесс, модели грузятся один раз."""
    from winescan.search.deep_match import DeepMatcher

    _init(images_dir, image_of)
    matcher = DeepMatcher()
    references: dict[str, tuple] = {}
    rows = []
    for number, task in enumerate(tasks, start=1):
        image, _ = _load_query(os.path.join(images_dir, task["image_path"]))
        box = task["box"]
        query_image = prepare(crop_box(image, box) if box else image)
        query_features = matcher.extract(query_image)
        best_visual = task["candidates"][0][1]
        for rank, (slug, visual) in enumerate(task["candidates"], start=1):
            if slug not in references:
                view = load_reference_view(slug) or reference_view(_state["uploads"] / image_of[slug])
                references[slug] = (view, matcher.extract(view))
            reference_image, reference_features = references[slug]
            verification = verify(query_image, query_features, reference_image, reference_features,
                                  matcher=matcher.match).as_dict()  # fmt: skip
            rows.append({"query_id": task["query_id"], "slug": slug, "rank": rank, "label": slug == task["expected"],
                         **candidate_features(visual, best_visual, verification), "inliers": verification["inliers"]})  # fmt: skip
        if number % 100 == 0:
            log.info("%s / %s", number, len(tasks))
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Признаки кандидатов для обучения слияния")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--rule", default=None, help="JSON правила выбора рамки; по умолчанию из box-selection.json или первая рамка")
    parser.add_argument("--box-ranker", default=None, help="обученный выбор рамки вместо правила (как в сервисе)")
    parser.add_argument("--indexes", default=None, help="другие индексы тех же моделей и видов через запятую")
    parser.add_argument("--name", default=None, help="имя таблицы вместо candidates_top<K>")
    parser.add_argument("--features", choices=("sift", "aliked"), default="sift",
                        help="локальные признаки: SIFT на процессах CPU или ALIKED + LightGlue на GPU")  # fmt: skip
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    cache = QueryCache.load(args.cache, indexes=args.indexes.split(",") if args.indexes else None)
    rule_path = paths.artifacts_dir / "cache" / args.cache / "box-selection.json"
    rule = json.loads(args.rule) if args.rule else (
        json.loads(rule_path.read_text())["best_rule"] if rule_path.exists() else {"prior": 1.0, "top1": 0.0, "margin": 0.0})
    ranker = BoxRanker.load(Path(args.box_ranker)) if args.box_ranker else None
    split = json.loads((paths.artifacts_dir / "cache" / args.cache / "meta.json").read_text())["split"]
    _, images_dir = load_split(split)
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    image_of = dict(zip(catalog["slug"], catalog["image_file"]))

    tasks = []
    for query_index, record in enumerate(cache.records[: args.limit] if args.limit else cache.records):
        slot = choose_slot(cache, query_index, rule)
        if ranker is not None:
            slots, rows, _ = query_rows(cache, query_index)
            slot = slots[ranker.choose(rows)] if slots else slot
        scores = cache.scores[query_index, slot]
        order = np.argsort(-scores)[: args.top]
        tasks.append({
            "query_id": record["query_id"], "image_path": record["image_path"], "expected": record["expected_slug"],
            "box": record["slots"][slot]["box"],
            "candidates": [(cache.wine_slugs[i], float(scores[i])) for i in order],
        })  # fmt: skip

    if args.features == "aliked":
        rows = _deep_rows(tasks, str(images_dir), image_of)
    else:
        workers = args.workers or min(16, os.cpu_count() or 1)
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(images_dir), image_of)) as pool:
            rows = [row for chunk in pool.map(_task, tasks, chunksize=4) for row in chunk]

    # согласие полей этикетки, если есть кэш VLM (eval.vlm_cache)
    vlm_fields = load_fields(args.cache)
    if vlm_fields:
        cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl")
        for row in rows:
            fields = vlm_fields.get(row["query_id"])
            if fields:
                row["field_score"] = field_score(cards[row["slug"]], LabelFields(**{**fields, "grapes": tuple(fields["grapes"])}))

    flags = {r["query_id"]: (r["in_phash_group"], r["shares_image"]) for r in cache.records}
    frame = pd.DataFrame(rows)
    frame["in_phash_group"] = frame["query_id"].map(lambda q: flags[q][0])
    frame["shares_image"] = frame["query_id"].map(lambda q: flags[q][1])
    out = paths.artifacts_dir / "cache" / args.cache / f"{args.name or f'candidates_top{args.top}'}.parquet"
    frame.to_parquet(out, index=False)
    print(f"{len(frame)} строк, {frame['query_id'].nunique()} запросов, верный в top-{args.top}: "
          f"{frame.groupby('query_id')['label'].any().mean():.3f} -> {out}")  # fmt: skip


if __name__ == "__main__":
    main()
