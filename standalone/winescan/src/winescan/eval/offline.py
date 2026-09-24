"""Офлайн-эксперименты по кэшу запросов: выбор рамки, отказ «не найдено», без моделей.

Запуск: ``python -m winescan.eval.offline box-selection --cache synth_v1``
        ``python -m winescan.eval.offline open-set --cache synth_v1``
        ``python -m winescan.eval.offline index-compare --cache synth_v2 --index-sets A,B C,D [--box-ranker ...]``

Скоры вин считаются из сохранённых эмбеддингов и индексов за секунды, поэтому правила можно
перебирать. Параметры подбираются на половине запросов (fold 0), проверяются на другой (fold 1).
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from winescan.config import get_paths
from winescan.eval.metrics import auroc, open_set_at_threshold
from winescan.search.box_selection import choose_box
from winescan.search.index import VectorIndex

KINDS = ("det", "full", "gt")


def _signature(index_meta: dict) -> tuple[str, str]:
    return index_meta["model_id"], index_meta.get("view_name", "full")


@dataclass
class QueryCache:
    records: list[dict]
    scores: np.ndarray  # float32 (запросы, слоты, вина): взвешенная сумма косинусов по индексам
    wine_slugs: list[str]
    slot_valid: np.ndarray  # bool (запросы, слоты)

    @classmethod
    def load(cls, name: str, indexes: list[str] | None = None, weights: list[float] | None = None) -> QueryCache:
        """Кэш со скорами по индексам кэша или по другим индексам тех же моделей и видов в том же
        порядке (например, мультиракурсная галерея): эмбеддинги запросов не зависят от галереи."""
        paths = get_paths()
        directory = paths.artifacts_dir / "cache" / name
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        with (directory / "queries.jsonl").open(encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]
        vectors = np.load(directory / "vectors.npy")
        if indexes and len(indexes) != len(meta["indexes"]):
            # векторы в кэше сохранены по индексу на вид; с другим их числом reshape склеит виды,
            # и падение случится глубоко в умножении матриц — сообщение было бы невнятным
            raise ValueError(f"в кэше {len(meta['indexes'])} индексов, передано {len(indexes)}: "
                             "подменять можно только галерею той же формы")  # fmt: skip
        loaded = [VectorIndex.load(paths.artifacts_dir / "index" / n) for n in indexes or meta["indexes"]]
        if indexes:
            for original, replacement in zip(meta["indexes"], loaded):
                # читаем только meta.json: загружать векторы исходных индексов ради сверки незачем
                source = json.loads((paths.artifacts_dir / "index" / original / "meta.json").read_text(encoding="utf-8"))
                if _signature(source) != _signature(replacement.meta):
                    raise ValueError(f"индекс {replacement.meta} не совпадает с {original} по модели и виду")
        return cls.from_arrays(records, vectors, loaded, weights or meta["index_weights"])

    @classmethod
    def from_arrays(cls, records, vectors, indexes, weights) -> QueryCache:
        queries, slots = vectors.shape[:2]
        flat = vectors.reshape(queries * slots, len(indexes), -1).astype(np.float32)
        total = np.zeros((queries * slots, len(indexes[0].wine_slugs)), dtype=np.float32)
        for position, (index, weight) in enumerate(zip(indexes, weights)):
            total += weight * index.wine_scores(flat[:, position])
        valid = np.zeros((queries, slots), dtype=bool)
        for query_index, record in enumerate(records):
            valid[query_index, : len(record["slots"])] = True
        return cls(records, total.reshape(queries, slots, -1), indexes[0].wine_slugs, valid)

    def slot_of_kind(self, query_index: int, kind: str) -> int | None:
        for slot_index, slot in enumerate(self.records[query_index]["slots"]):
            if slot["kind"] == kind:
                return slot_index
        return None

    def expected_index(self, query_index: int) -> int | None:
        slug = self.records[query_index]["expected_slug"]
        return self.wine_slugs.index(slug) if slug in self._slug_position else None

    def __post_init__(self) -> None:
        self._slug_position = {slug: i for i, slug in enumerate(self.wine_slugs)}


def top2(scores: np.ndarray, exclude: int | None = None) -> tuple[int, float, float]:
    """Индекс лучшего вина, его скор и отрыв от второго (опционально без одного вина — leave-one-out)."""
    values = scores.copy()
    if exclude is not None:
        values[exclude] = -np.inf
    first = int(np.argmax(values))
    best = float(values[first])
    values[first] = -np.inf
    return first, best, best - float(np.max(values))


def slot_features(cache: QueryCache, query_index: int, slot_index: int) -> dict:
    slot = cache.records[query_index]["slots"][slot_index]
    first, best, margin = top2(cache.scores[query_index, slot_index])
    return {"prior": slot.get("prior", 0.0), "top1": best, "margin": margin, "wine": first}


def choose_slot(cache: QueryCache, query_index: int, rule: dict) -> int:
    """Выбор рамки по search.box_selection среди рамок детектора; без рамок детектора — весь кадр."""
    det_slots = [i for i, s in enumerate(cache.records[query_index]["slots"]) if s["kind"] == "det"]
    if not det_slots:
        return cache.slot_of_kind(query_index, "full")
    features = [slot_features(cache, query_index, i) for i in det_slots]
    best = choose_box([f["prior"] for f in features], [f["top1"] for f in features], [f["margin"] for f in features], rule)
    return det_slots[best]


def evaluate_rule(cache: QueryCache, query_indices: list[int], rule: dict | str) -> dict:
    correct, top5 = [], []
    for query_index in query_indices:
        expected = cache.expected_index(query_index)
        if expected is None:
            continue
        if isinstance(rule, str):
            slot = cache.slot_of_kind(query_index, rule) if rule != "det0" else choose_slot(
                cache, query_index, {"prior": 1.0, "top1": 0.0, "margin": 0.0})
        else:
            slot = choose_slot(cache, query_index, rule)
        if slot is None:
            continue
        order = np.argsort(-cache.scores[query_index, slot])[:5]
        correct.append(order[0] == expected)
        top5.append(expected in order)
    return {"queries": len(correct), "top1": float(np.mean(correct)) if correct else 0.0,
            "top5": float(np.mean(top5)) if top5 else 0.0}  # fmt: skip


def folds(cache: QueryCache) -> tuple[list[int], list[int]]:
    rng = np.random.default_rng(0)
    order = rng.permutation(len(cache.records))
    half = len(order) // 2
    return sorted(order[:half].tolist()), sorted(order[half:].tolist())


def box_selection(cache: QueryCache) -> dict:
    fit, check = folds(cache)
    grid = [{"prior": p, "top1": t, "margin": m}
            for p, t, m in itertools.product([0.0, 0.01, 0.02, 0.05, 0.1], [0.0, 1.0], [0.0, 1.0, 3.0])
            if t or m or p]  # fmt: skip
    best_rule = max(grid, key=lambda rule: evaluate_rule(cache, fit, rule)["top1"])
    report = {"best_rule": best_rule}
    for name, rule in (("det0 (v3, одна рамка)", "det0"), ("весь кадр", "full"), ("настоящая рамка", "gt"),
                       ("выбор по поиску", best_rule)):  # fmt: skip
        report[name] = {"fit": evaluate_rule(cache, fit, rule), "check": evaluate_rule(cache, check, rule)}
    return report


def open_set(cache: QueryCache, rule: dict | str = "det0") -> dict:
    """Позитивы — обычные запросы; негативы — те же кадры без своего вина в индексе (leave-one-out).
    Вина с общим эталоном в негативы не берутся: визуально они остаются «в каталоге»."""
    positive_conf, positive_correct, negative_conf = [], [], []
    for query_index, record in enumerate(cache.records):
        expected = cache.expected_index(query_index)
        if expected is None:
            continue
        slot = choose_slot(cache, query_index, {"prior": 1.0, "top1": 0.0, "margin": 0.0}) if rule == "det0" else (
            choose_slot(cache, query_index, rule) if isinstance(rule, dict) else cache.slot_of_kind(query_index, rule))
        scores = cache.scores[query_index, slot]
        first, best, _ = top2(scores)
        positive_conf.append(best)
        positive_correct.append(first == expected)
        if not record["shares_image"]:
            negative_conf.append(top2(scores, exclude=expected)[1])
    positive_conf, positive_correct, negative_conf = map(np.asarray, (positive_conf, positive_correct, negative_conf))
    thresholds = np.quantile(np.concatenate([positive_conf, negative_conf]), np.linspace(0, 1, 201))
    sweep = [open_set_at_threshold(positive_correct, positive_conf, negative_conf, float(t)) for t in thresholds]
    best = max(sweep, key=lambda s: s["open_set_accuracy"])
    return {
        "confidence": "визуальный скор top-1",
        "auroc_correct_vs_negative": auroc(positive_conf[positive_correct], negative_conf),
        "positives": int(len(positive_conf)),
        "negatives": int(len(negative_conf)),
        "best_threshold": best,
        "at_0.74": open_set_at_threshold(positive_correct, positive_conf, negative_conf, 0.74),
    }


def index_compare(cache_name: str, index_sets: list[list[str]], box_ranker_path: str | None) -> dict:
    """top-1 / top-5 на fold 1 для первой рамки, рамки обучаемого выбора и настоящей рамки по разным галереям."""
    from winescan.eval.train_box_ranker import evaluate as evaluate_ranker  # импорт здесь: модуль сам импортирует offline
    from winescan.search.box_ranker import BoxRanker

    ranker = BoxRanker.load(Path(box_ranker_path)) if box_ranker_path else None
    result = {}
    for names in index_sets:
        cache = QueryCache.load(cache_name, indexes=names)
        _, check = folds(cache)
        row = {"first_box": evaluate_rule(cache, check, "det0"), "true_box": evaluate_rule(cache, check, "gt")}
        if ranker is not None:
            row["box_ranker"] = evaluate_ranker(cache, check, ranker)
        result["+".join(names)] = row
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Офлайн-эксперименты по кэшу запросов")
    parser.add_argument("experiment", choices=("box-selection", "open-set", "index-compare"))
    parser.add_argument("--cache", required=True, help="папка в artifacts/cache")
    parser.add_argument("--index-sets", nargs="*", default=None,
                        help="для index-compare: наборы индексов через запятую, по одному на вариант")  # fmt: skip
    parser.add_argument("--box-ranker", default=None, help="для index-compare: модель выбора рамки")
    args = parser.parse_args(argv)
    if args.experiment == "index-compare":
        result = index_compare(args.cache, [s.split(",") for s in args.index_sets], args.box_ranker)
        for name, row in result.items():
            print(name)
            for variant, metrics in row.items():
                print(f"   {variant:11} top-1 {metrics['top1']:.3f}  top-5 {metrics['top5']:.3f}")
        return
    cache = QueryCache.load(args.cache)
    result = box_selection(cache) if args.experiment == "box-selection" else open_set(cache)
    out = get_paths().artifacts_dir / "cache" / args.cache / f"{args.experiment}.json"
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=float))


if __name__ == "__main__":
    main()
