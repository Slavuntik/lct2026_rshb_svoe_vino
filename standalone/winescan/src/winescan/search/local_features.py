"""Слой 3: хранилище SIFT-признаков и подготовленных вырезок эталонов, чтобы сервис не считал их на лету.

Запуск: ``python -m winescan.search.local_features [--workers 16]``

artifacts/index/local_features/:
    descriptors.npy  uint8 (все дескрипторы подряд; квантование float32 -> uint8, ошибка ≤ 0,5)
    keypoints.npy    float32 (x, y)
    index.json       slug -> [начало, конец]
artifacts/index/reference_views/<slug>.png
    вырезка упаковки (RGBA) в масштабе признаков: нужна search.verify для сравнения пикселей
    после выравнивания; декодировать исходные WEBP до 9506 px на каждый запрос слишком долго.
Файлы признаков открываются через memory map: в память попадают только нужные кандидаты.
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
from winescan.search.local_match import Features, extract, prepare
from winescan.vision.preprocess import cutout

log = logging.getLogger("winescan.search.local_features")

REFERENCE_SIDE = 1024
STORE_NAME = "local_features"
VIEWS_NAME = "reference_views"


def _reference_cutout(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        image = image.copy()
    image.thumbnail((REFERENCE_SIDE, REFERENCE_SIDE))
    return cutout(image)


def reference_features(path: Path) -> Features:
    """SIFT эталона: уменьшить до 1024, вырезать упаковку, извлечь признаки."""
    return extract(_reference_cutout(path).convert("RGB"))


def reference_view(path: Path) -> Image.Image:
    """Вырезка эталона (RGBA) в масштабе признаков — для search.verify."""
    return prepare(_reference_cutout(path))


def load_reference_view(slug: str, views_dir: Path | None = None) -> Image.Image | None:
    path = (views_dir or get_paths().artifacts_dir / "index" / VIEWS_NAME) / f"{slug}.png"
    if not path.exists():
        return None
    with Image.open(path) as image:
        image.load()
        return image.copy()


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


def _task(item: tuple[str, str, str]) -> tuple[str, Features]:
    slug, path, views_dir = item
    package = _reference_cutout(Path(path))
    prepare(package).save(Path(views_dir) / f"{slug}.png")
    return slug, extract(package.convert("RGB"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SIFT-признаки и вырезки всех эталонов")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    setup_logging()

    paths = get_paths()
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    wines = catalog.dropna(subset=["image_file"]).sort_values("slug")
    views_dir = paths.artifacts_dir / "index" / VIEWS_NAME
    views_dir.mkdir(parents=True, exist_ok=True)
    items = [(slug, str(paths.uploads_dir / f), str(views_dir)) for slug, f in zip(wines["slug"], wines["image_file"])]

    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers or min(16, os.cpu_count() or 1)) as pool:
        results = list(pool.map(_task, items, chunksize=8))
    out_dir = paths.artifacts_dir / "index" / STORE_NAME
    LocalFeatureStore.save(out_dir, results)
    size_mb = sum(f.stat().st_size for f in out_dir.iterdir()) / 2**20
    views_mb = sum(f.stat().st_size for f in views_dir.iterdir()) / 2**20
    log.info("SIFT-признаки %s эталонов за %.0f с: %.0f МБ признаков, %.0f МБ вырезок -> %s",
             len(results), time.monotonic() - started, size_mb, views_mb, out_dir)  # fmt: skip


if __name__ == "__main__":
    main()
