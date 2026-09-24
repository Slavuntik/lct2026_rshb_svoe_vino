"""Обучение слияния сигналов и порога отказа по таблице кандидатов.

Запуск: ``python -m winescan.eval.train_fusion --cache synth_v1,synth_v2 [--top 5] [--table NAME] --out configs/fusion_v2.json``

1. Запросы делятся пополам (fold 0 — подбор, fold 1 — проверка).
2. Базовые линии на fold 1: только визуальный скор; прежнее ручное слияние (визуальный +
   0,15 × бонус SIFT).
3. Регуляризация C подбирается на внутреннем разбиении fold 0, модель обучается на всём fold 0.
4. Порог отказа (минимальная вероятность лучшего кандидата) подбирается на fold 0 по
   leave-one-out негативам — те же кандидаты без верного вина — и проверяется на fold 1.
5. Модель и порог сохраняются в JSON (сервис: WINESCAN_FUSION), отчёт — рядом с кэшем.
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np
import pandas as pd

from winescan.config import PROJECT_ROOT, get_paths
from winescan.eval.metrics import auroc, open_set_at_threshold
from winescan.search.fusion import FEATURES, FusionModel, train
from winescan.search.rerank import local_bonus

C_GRID = (0.03, 0.1, 0.3, 1.0, 3.0, 10.0)


def group_queries(frame: pd.DataFrame) -> dict[str, list[dict]]:
    queries: dict[str, list[dict]] = {}
    for row in frame.itertuples(index=False):
        record = row._asdict()
        queries.setdefault(record["query_id"], []).append(
            {"slug": record["slug"], "label": bool(record["label"]), "features": {n: float(record[n]) for n in FEATURES},
             "inliers": int(record.get("inliers", 0)), "in_phash_group": bool(record["in_phash_group"]),
             "shares_image": bool(record["shares_image"])}  # fmt: skip
        )
    return queries


def split_queries(query_ids: list[str], seed: int = 0) -> tuple[list[str], list[str]]:
    order = np.random.default_rng(seed).permutation(sorted(query_ids))
    half = len(order) // 2
    return sorted(order[:half].tolist()), sorted(order[half:].tolist())


def rank_top1(candidates: list[dict], scorer) -> dict:
    return max(candidates, key=scorer)


def evaluate(queries: dict[str, list[dict]], ids: list[str], scorer) -> dict:
    """top-1 по всем запросам (нет верного в top-K — промах) и по подвыборкам."""
    parts = {"all": [], "in_phash_group": [], "shares_image": []}
    for query_id in ids:
        candidates = queries[query_id]
        hit = rank_top1(candidates, scorer)["label"]
        parts["all"].append(hit)
        if candidates[0]["in_phash_group"]:
            parts["in_phash_group"].append(hit)
        if candidates[0]["shares_image"]:
            parts["shares_image"].append(hit)
    return {name: (float(np.mean(values)) if values else None) for name, values in parts.items()} | {"queries": len(ids)}


def without_true_wine(rest: list[dict]) -> list[dict]:
    """Кандидаты leave-one-out негатива с признаками, какими они были бы без верного вина в индексе.

    ``gap`` — отставание от лучшего визуального скора. Если оставить его посчитанным с верным вином,
    у негатива gap < 0, и модель отличает его по подсказке, которой у настоящего вина вне каталога
    нет. До исправления это завышало AUROC отказа с 0,815 до 0,95 (WORKLOG, «Утечка в негативах»)."""
    best = max(c["features"]["visual"] for c in rest)
    return [{**c, "features": {**c["features"], "gap": c["features"]["visual"] - best}} for c in rest]


def open_set(
    model: FusionModel,
    queries: dict[str, list[dict]],
    ids: list[str],
    threshold: float | None = None,
    max_false_reject: float | None = None,
) -> dict:
    """Порог отказа по логиту лучшего кандидата. Без явного порога — максимум точности на
    открытом множестве или (``max_false_reject``) наибольший порог, при котором вина из
    каталога отклоняются не чаще заданной доли."""
    positive_conf, positive_correct, negative_conf = [], [], []
    for query_id in ids:
        candidates = queries[query_id]
        ranked = model.rank(candidates)
        positive_conf.append(ranked[0][0])
        positive_correct.append(ranked[0][1]["label"])
        rest = [c for c in candidates if not c["label"]]
        if len(rest) == len(candidates) or candidates[0]["shares_image"] or not rest:
            continue  # верного нет в top-K или эталон общий — такой запрос не даёт честного негатива
        negative_conf.append(model.rank(without_true_wine(rest))[0][0])
    positive_conf, positive_correct, negative_conf = map(np.asarray, (positive_conf, positive_correct, negative_conf))
    if threshold is None and max_false_reject is not None:
        threshold = float(np.quantile(positive_conf, max_false_reject))
    elif threshold is None:
        grid = np.quantile(np.concatenate([positive_conf, negative_conf]), np.linspace(0, 1, 201))
        threshold = float(max(grid, key=lambda t: open_set_at_threshold(positive_correct, positive_conf, negative_conf, float(t))["open_set_accuracy"]))
    return {"auroc": auroc(positive_conf[positive_correct], negative_conf), "threshold_logit": threshold,
            **open_set_at_threshold(positive_correct, positive_conf, negative_conf, threshold)}  # fmt: skip


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Обучение слияния сигналов")
    parser.add_argument("--cache", required=True, help="кэш или несколько через запятую (обучение на всех)")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--table", default=None, help="имя таблицы кандидатов вместо candidates_top<K>")
    parser.add_argument("--out", required=True, help="куда сохранить модель, например configs/fusion_v3.json")
    parser.add_argument("--max-false-reject", type=float, default=0.02, help="доля ложных отказов для порога сервиса")
    args = parser.parse_args(argv)

    paths = get_paths()
    caches = args.cache.split(",")
    queries, members = {}, {}
    for cache in caches:
        frame = pd.read_parquet(paths.artifacts_dir / "cache" / cache / f"{args.table or f'candidates_top{args.top}'}.parquet")
        frame["query_id"] = cache + "/" + frame["query_id"].astype(str)  # номера запросов разных кэшей совпадают
        part = group_queries(frame)
        queries |= part
        members[cache] = set(part)
    fit, check = split_queries(list(queries))
    inner_fit, inner_check = split_queries(fit, seed=1)

    def rows(ids):
        return [candidate for query_id in ids for candidate in queries[query_id]]

    selection = {c: evaluate(queries, inner_check, lambda cand, m=train(rows(inner_fit), c=c): m.logit(cand["features"]))["all"]
                 for c in C_GRID}  # fmt: skip
    best_c = max(selection, key=selection.get)
    model = train(rows(fit), c=best_c)

    report = {
        "cache": args.cache,
        "top": args.top,
        "queries": {"fit": len(fit), "check": len(check)},
        "c_selection_inner_top1": selection,
        "best_c": best_c,
        "check": {
            "visual_only": evaluate(queries, check, lambda c: c["features"]["visual"]),
            "legacy_sift_0.15": evaluate(queries, check, lambda c: c["features"]["visual"] + 0.15 * local_bonus(c["inliers"])),
            "fusion": evaluate(queries, check, lambda c: model.logit(c["features"])),
        },
        "upper_bound_true_in_top_k": float(np.mean([any(c["label"] for c in queries[q]) for q in check])),
    }
    if len(caches) > 1:
        report["check_by_cache"] = {
            cache: {"fusion": evaluate(queries, ids, lambda c: model.logit(c["features"])),
                    "legacy_sift_0.15": evaluate(queries, ids, lambda c: c["features"]["visual"] + 0.15 * local_bonus(c["inliers"]))}
            for cache in caches
            for ids in [[q for q in check if q in members[cache]]]
        }  # fmt: skip
    fit_open = open_set(model, queries, fit)
    # на реальном фото Массандры порог «максимум точности» с синтетики (0,74) отклонил верный ответ
    # (логит 0,50): признаки проверки на реальных фото слабее. Поэтому в сервис идёт осторожный порог —
    # не больше args.max_false_reject ложных отказов винам из каталога на синтетике
    fit_conservative = open_set(model, queries, fit, max_false_reject=args.max_false_reject)
    report["open_set"] = {
        "max_accuracy": {"fit": fit_open, "check": open_set(model, queries, check, fit_open["threshold_logit"])},
        f"false_reject_le_{args.max_false_reject}": {
            "fit": fit_conservative,
            "check": open_set(model, queries, check, fit_conservative["threshold_logit"]),
        },
    }
    model.meta.update({"cache": args.cache, "table": args.table, "top": args.top, "reject_logit": fit_conservative["threshold_logit"],
                       "reject_rule": f"доля ложных отказов винам из каталога на синтетике ≤ {args.max_false_reject}",
                       "reject_logit_max_accuracy": fit_open["threshold_logit"],
                       "check_top1": report["check"]["fusion"]["all"]})  # fmt: skip

    out = PROJECT_ROOT / args.out
    model.save(out)
    report_dir = paths.artifacts_dir / "cache" / (caches[0] if len(caches) == 1 else "")
    (report_dir / f"fusion_report_{out.stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("веса:", json.dumps(model.weights, ensure_ascii=False))
    print("модель ->", out)


if __name__ == "__main__":
    main()
