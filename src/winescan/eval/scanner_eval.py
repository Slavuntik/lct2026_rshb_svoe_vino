"""Сквозной прогон сервисного Scanner на выборке: те же выбор рамки, слияние, отказ и задержки, что в API.

Запуск: ``CUDA_VISIBLE_DEVICES=3 python -m winescan.eval.scanner_eval --split synth_v2 --tag default [--limit N]``

Конфиг — ``ScannerConfig.from_env()`` (переменные WINESCAN_*), поэтому вариант задаётся окружением,
а ``--tag`` только называет прогон: artifacts/eval/scanner__<split>__<tag>/. В отличие от eval.run,
здесь учитывается решение «не найдено»: доля ложных отказов, неверных ответов и отказов винам вне
каталога (для сплита public).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from PIL import Image

from winescan.config import PROJECT_ROOT, get_paths
from winescan.eval.run import load_split, markdown_table, summarize
from winescan.logging_setup import setup_logging

log = logging.getLogger("winescan.eval.scanner_eval")


def decision_summary(records: list[dict]) -> dict:
    """Итог решения found / not_found. Для вин из каталога: верный ответ, неверный ответ, отказ.
    Для вин вне каталога: отказ (верно) или ответ (ошибка)."""
    in_catalog = [r for r in records if r["expected_slug"]]
    outside = [r for r in records if not r["expected_slug"]]
    found = [r for r in in_catalog if r["status"] == "found"]
    correct = sum(r["answer_slug"] == r["expected_slug"] for r in found)

    def share(value: int, total: int) -> float | None:
        return value / total if total else None

    return {
        "in_catalog": len(in_catalog),
        "answered_correct": share(correct, len(in_catalog)),
        "answered_wrong": share(len(found) - correct, len(in_catalog)),
        "rejected": share(len(in_catalog) - len(found), len(in_catalog)),
        "precision_of_answers": share(correct, len(found)),
        "out_of_catalog": len(outside),
        "out_of_catalog_rejected": share(sum(r["status"] == "not_found" for r in outside), len(outside)),
        "open_set_accuracy": share(correct + sum(r["status"] == "not_found" for r in outside), len(records)),
    }


def _resolve(path: str) -> Path:
    return Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path


def _query_ids(cache: str) -> list[str]:
    with (get_paths().artifacts_dir / "cache" / cache / "queries.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line)["query_id"] for line in fh]


def holdout_ids(split: str, box_ranker_meta: dict | None, fusion_meta: dict | None) -> set[str]:
    """Запросы сплита, которые не видели при обучении ни выбор рамки (fold 1 из offline.folds), ни
    слияние (проверочная половина train_fusion.split_queries по всем его кэшам). Разбиения у двух
    моделей разные, поэтому остаётся примерно четверть запросов."""
    from winescan.eval.offline import folds
    from winescan.eval.train_fusion import split_queries

    ids = _query_ids(split)
    keep = set(ids)
    if box_ranker_meta and split in box_ranker_meta.get("caches", []):
        _, check = folds(SimpleNamespace(records=ids))
        keep &= {ids[i] for i in check}
    caches = (fusion_meta or {}).get("cache", "").split(",")
    if split in caches:
        _, check = split_queries([f"{cache}/{q}" for cache in caches for q in _query_ids(cache)])
        keep &= {q.split("/", 1)[1] for q in check if q.startswith(f"{split}/")}
    return keep


def main(argv: list[str] | None = None) -> None:
    from winescan.service.pipeline import Scanner, ScannerConfig

    parser = argparse.ArgumentParser(description="Сквозной прогон Scanner с конфигом сервиса")
    parser.add_argument("--split", required=True)
    parser.add_argument("--tag", default="default", help="имя варианта конфига (сам конфиг — из WINESCAN_*)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--holdout", action="store_true", help="только запросы, не использованные при обучении моделей")
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    manifest, images_dir = load_split(args.split)
    for flag in ("in_phash_group", "shares_image"):
        manifest[flag] = manifest[flag].astype(str).eq("True")
    if args.limit:
        manifest = manifest.sample(n=min(args.limit, len(manifest)), random_state=0)

    config = ScannerConfig.from_env()
    if args.holdout:
        # те же разбиения, что при обучении: выбор рамки и слияние обучены на своих половинах запросов
        scanner_models = {"box_ranker_meta": None, "fusion_meta": None}
        if config.box_ranker_path and config.box_candidates > 1:
            from winescan.search.box_ranker import BoxRanker

            scanner_models["box_ranker_meta"] = BoxRanker.load(_resolve(config.box_ranker_path)).meta
        if config.fusion_path:
            from winescan.search.fusion import FusionModel

            scanner_models["fusion_meta"] = FusionModel.load(_resolve(config.fusion_path)).meta
        manifest = manifest[manifest["query_id"].isin(holdout_ids(args.split, **scanner_models))]
        args.tag += "__holdout"
    scanner = Scanner(config)
    scanner.warmup()

    records = []
    started = time.monotonic()
    for position, row in enumerate(manifest.itertuples(index=False), start=1):
        with Image.open(images_dir / row.image_path) as image:
            image.load()
            result = scanner.scan(image)
        slugs = [c["slug"] for c in result.top5]
        expected = row.slug or ""
        records.append({
            "query_id": row.query_id, "expected_slug": expected, "status": result.status,
            "answer_slug": result.slug or "", "predicted_slug": slugs[0],
            "rank": slugs.index(expected) + 1 if expected in slugs else None,
            "score_top1": result.confidence["score_top1"], "margin": result.confidence["margin_top1_top2"] or 0.0,
            "band": result.confidence["band"], "reason": result.confidence["decision_reason"],
            "top_slugs": ";".join(slugs), "box": "" if result.box is None else ",".join(f"{v:.0f}" for v in result.box),
            "in_phash_group": row.in_phash_group, "shares_image": row.shares_image,
            **{f"{k}_ms": v for k, v in result.timings_ms.items()},
        })  # fmt: skip
        if position % 100 == 0:
            log.info("%s / %s", position, len(manifest))

    predictions = pd.DataFrame(records)
    total = predictions["total_ms"].to_numpy()
    timing_columns = [c for c in predictions.columns if c.endswith("_ms")]
    metrics = {
        "split": args.split,
        "tag": args.tag,
        "config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in dataclasses.asdict(config).items()},
        "crop": f"сервис, рамок {config.box_candidates}" + (", выбор обучен" if config.box_ranker_path else ""),
        "ocr": config.use_ocr and config.fusion_path is None,
        "queries": len(predictions),
        "seconds": round(time.monotonic() - started, 1),
        **(summarize(predictions) if (predictions["expected_slug"] != "").any() else {}),
        "decision": decision_summary(records),
        "latency_ms": {
            **{f"{c[:-3]}_mean": float(predictions[c].mean()) for c in timing_columns},
            "total_p50": float(np.percentile(total, 50)),
            "total_p95": float(np.percentile(total, 95)),
        },
    }
    out_dir = paths.artifacts_dir / "eval" / f"scanner__{args.split}__{args.tag}{f'__limit{args.limit}' if args.limit else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(out_dir / "predictions.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=float), encoding="utf-8")

    if "all" in metrics:
        print(markdown_table(metrics))
    print(json.dumps(metrics["decision"], ensure_ascii=False, indent=2))
    latency = metrics["latency_ms"]
    print(f"задержка, мс: p50 {latency['total_p50']:.0f}, p95 {latency['total_p95']:.0f} -> {out_dir}")


if __name__ == "__main__":
    main()
