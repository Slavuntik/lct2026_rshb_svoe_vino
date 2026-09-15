"""Слой 3: хранилище SIFT-признаков эталонов, чтобы сервис не считал их на лету.

Запуск: ``python -m winescan.search.local_features [--workers 16]``

artifacts/index/local_features/:
    descriptors.npy  uint8 (все дескрипторы подряд; квантование float32 -> uint8, ошибка ≤ 0,5)
    keypoints.npy    float32 (x, y)
    index.json       slug -> [начало, конец]
Файлы открываются через memory map: в память попадают только нужные кандидаты.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from winescan.config import get_paths
from winescan.logging_setup import setup_logging
from winescan.search.local_match import Features, extract
from winescan.vision.preprocess import cutout

log = logging.getLogger("winescan.search.local_features")

REFERENCE_SIDE = 1024
STORE_NAME = "local_features"


def reference_features(path: Path) -> Features:
    """SIFT эталона: уменьшить до 1024, вырезать упаковку, извлечь признаки."""
    with Image.open(path) as image:
        image.load()
        image = image.copy()
    image.thumbnail((REFERENCE_SIDE, REFERENCE_SIDE))
    return extract(cutout(image).convert("RGB"))


class LocalFeatureStore:
    def __init__(self, directory: Path):
        self.descriptors = np.load(directory / "descriptors.npy", mmap_mode="r")
        self.keypoints = np.load(directory / "keypoints.npy", mmap_mode="r")
        self.ranges: dict[str, list[int]] = json.loads((directory / "index.json").read_text(encoding="utf-8"))

    def __contains__(self, slug: str) -> bool:
        return slug in self.ranges

    def get(self, slug: str) -> Features:
        start, end = self.ranges[slug]
        descriptors = np.asarray(self.descriptors[start:end], dtype=np.float32) if end > start else None
        return Features(np.asarray(self.keypoints[start:end], dtype=np.float32), descriptors)

    @staticmethod
    def save(directory: Path, items: list[tuple[str, Features]]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        ranges, keypoints, descriptors, cursor = {}, [], [], 0
        for slug, features in items:
            count = 0 if features.descriptors is None else len(features.descriptors)
            ranges[slug] = [cursor, cursor + count]
            if count:
                keypoints.append(features.keypoints[:count].astype(np.float32))
                descriptors.append(np.clip(np.rint(features.descriptors), 0, 255).astype(np.uint8))
            cursor += count
        np.save(directory / "keypoints.npy", np.concatenate(keypoints) if keypoints else np.zeros((0, 2), np.float32))
        np.save(directory / "descriptors.npy", np.concatenate(descriptors) if descriptors else np.zeros((0, 128), np.uint8))
        (directory / "index.json").write_text(json.dumps(ranges, ensure_ascii=False), encoding="utf-8")


def _task(item: tuple[str, str]) -> tuple[str, Features]:
    slug, path = item
    return slug, reference_features(Path(path))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Посчитать SIFT-признаки всех эталонов")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    wines = catalog.dropna(subset=["image_file"]).sort_values("slug")
    items = [(slug, str(paths.uploads_dir / f)) for slug, f in zip(wines["slug"], wines["image_file"])]

    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers or min(16, os.cpu_count() or 1)) as pool:
        results = list(pool.map(_task, items, chunksize=8))
    out_dir = paths.artifacts_dir / "index" / STORE_NAME
    LocalFeatureStore.save(out_dir, results)
    size_mb = sum(f.stat().st_size for f in out_dir.iterdir()) / 2**20
    log.info("SIFT-признаки %s эталонов за %.0f с, %.0f МБ -> %s", len(results), time.monotonic() - started, size_mb, out_dir)


if __name__ == "__main__":
    main()
