"""Кэш запросов для быстрых офлайн-экспериментов (без повторного запуска моделей).

Запуск: ``python -m winescan.eval.query_cache --split synth_v1 [--boxes 3] [--limit N]``

Для каждого кадра: все детекции OWLv2, до K рамок-кандидатов (по априорному весу, без почти
совпадающих), весь кадр и — для синтетики — настоящая рамка; эмбеддинги кропа каждой рамки по
каждому индексу сервиса. Дальше выбор рамки, слияние, отказ и пороги считаются офлайн.

artifacts/cache/<split>/:
    queries.jsonl  запрос: ожидаемый slug, флаги, размер кадра, детекции, слоты (рамки)
    vectors.npy    float16 (запросы, слоты, индексы, dim); пустой слот — нули
    meta.json      индексы, веса, число слотов, время
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime

import numpy as np

from winescan.config import get_paths
from winescan.eval.run import _load_query, load_split
from winescan.logging_setup import setup_logging
from winescan.search.multi import MultiIndexSearcher
from winescan.service.pipeline import ScannerConfig
from winescan.vision.detector import PackageDetector, rank_packages, select_candidates

log = logging.getLogger("winescan.eval.query_cache")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Кэш детекций и эмбеддингов запросов")
    parser.add_argument("--split", required=True)
    parser.add_argument("--boxes", type=int, default=3, help="рамок-кандидатов детектора")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    manifest, images_dir = load_split(args.split)
    for flag in ("in_phash_group", "shares_image"):
        manifest[flag] = manifest[flag].astype(str).eq("True")
    if args.limit:
        manifest = manifest.sample(n=min(args.limit, len(manifest)), random_state=0)
    rows = list(manifest.itertuples(index=False))

    config = ScannerConfig()
    searcher = MultiIndexSearcher(list(config.indexes), list(config.index_weights), args.device)
    detector = PackageDetector(device=args.device)
    dims = {index.vectors.shape[1] for index in searcher.indexes}
    if len(dims) != 1:
        raise ValueError("кэш рассчитан на индексы одной размерности")
    slots_max = args.boxes + 2  # рамки детектора + весь кадр + настоящая рамка
    vectors = np.zeros((len(rows), slots_max, len(searcher.indexes), dims.pop()), dtype=np.float16)

    records, started = [], time.monotonic()
    for start in range(0, len(rows), args.batch_size):
        items = []
        for query_index, row in enumerate(rows[start : start + args.batch_size], start=start):
            image, scale = _load_query(images_dir / row.image_path)
            began = time.perf_counter()
            detections = detector.detect(image)
            detect_ms = (time.perf_counter() - began) * 1000
            slots = [
                {"kind": "det", "box": list(d.box), "det_score": d.score, "prior": weight, "label": d.label}
                for d, weight in select_candidates(rank_packages(detections, image.size), args.boxes)
            ]
            slots.append({"kind": "full", "box": None})
            if getattr(row, "bbox", ""):
                slots.append({"kind": "gt", "box": [float(v) * scale for v in row.bbox.split(",")]})
            for slot_index, slot in enumerate(slots):
                items.append((query_index, slot_index, image, tuple(slot["box"]) if slot["box"] else None))
            records.append(
                {
                    "query_id": row.query_id,
                    "image_path": row.image_path,
                    "expected_slug": row.slug or "",
                    "in_phash_group": bool(row.in_phash_group),
                    "shares_image": bool(row.shares_image),
                    "size": list(image.size),
                    "detect_ms": round(detect_ms, 1),
                    "detections": [[*d.box, d.score, d.label] for d in detections],
                    "slots": slots,
                }
            )
        embedded = searcher.embed_views([item[2] for item in items], [item[3] for item in items])
        for position, (query_index, slot_index, _, _) in enumerate(items):
            for index_position, index_vectors in enumerate(embedded):
                vectors[query_index, slot_index, index_position] = index_vectors[position]
        log.info("%s / %s", min(start + args.batch_size, len(rows)), len(rows))

    out_dir = paths.artifacts_dir / "cache" / (args.split + (f"__limit{args.limit}" if args.limit else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "queries.jsonl").open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    np.save(out_dir / "vectors.npy", vectors)
    (out_dir / "meta.json").write_text(
        json.dumps(
            {"split": args.split, "indexes": list(config.indexes), "index_weights": list(config.index_weights),
             "slots_max": slots_max, "boxes": args.boxes, "queries": len(records),
             "seconds": round(time.monotonic() - started, 1), "built_at": datetime.now().isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )  # fmt: skip
    log.info("кэш %s запросов за %.0f с -> %s", len(records), time.monotonic() - started, out_dir)


if __name__ == "__main__":
    main()
