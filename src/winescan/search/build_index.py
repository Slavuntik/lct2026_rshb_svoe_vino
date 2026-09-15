"""Индекс эталонов каталога: artifacts/index/<name>/.

Запуск: ``python -m winescan.search.build_index [--model google/siglip2-base-patch16-224] [--name NAME]``

Требует собранный каталог (``python -m winescan.catalog.build``).
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image

from winescan.config import get_paths
from winescan.search.index import VectorIndex
from winescan.vision.embedder import DEFAULT_EMBEDDER, ImageEmbedder
from winescan.vision.preprocess import reference_view

log = logging.getLogger("winescan.search.build_index")

MAX_REFERENCE_SIDE = 1024


def load_reference_view(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        image = image.copy()
    image.thumbnail((MAX_REFERENCE_SIDE, MAX_REFERENCE_SIDE))
    return reference_view(image)


def build(model_id: str, name: str | None, batch_size: int, device: str | None) -> Path:
    paths = get_paths()
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    wines = catalog.dropna(subset=["image_file"]).sort_values("slug").reset_index(drop=True)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        views = list(pool.map(load_reference_view, (paths.uploads_dir / f for f in wines["image_file"])))
    prepared = time.monotonic()

    embedder = ImageEmbedder(model_id, device=device)
    vectors = embedder.embed(views, batch_size=batch_size)
    embedded = time.monotonic()

    index = VectorIndex(
        slugs=wines["slug"].tolist(),
        vectors=vectors,
        meta={
            "model_id": model_id,
            "view": "reference_view: cutout -> белый квадрат",
            "wines": len(wines),
            "dim": int(vectors.shape[1]),
            "device": embedder.device,
            "prepare_seconds": round(prepared - started, 1),
            "embed_seconds": round(embedded - prepared, 1),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    out_dir = paths.artifacts_dir / "index" / (name or model_id.split("/")[-1])
    index.save(out_dir)
    log.info("индекс %s: %s вин, dim %s, подготовка %.0f с, эмбеддинги %.0f с",
             out_dir, len(wines), vectors.shape[1], prepared - started, embedded - prepared)  # fmt: skip
    return out_dir


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Построить векторный индекс эталонов")
    parser.add_argument("--model", default=DEFAULT_EMBEDDER)
    parser.add_argument("--name", default=None, help="папка в artifacts/index (по умолчанию имя модели)")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build(args.model, args.name, args.batch_size, args.device)


if __name__ == "__main__":
    main()
