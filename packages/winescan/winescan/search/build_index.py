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
from winescan.logging_setup import setup_logging
from winescan.search.index import VectorIndex
from winescan.vision.cylinder import rotate_cylinder
from winescan.vision.embedder import DEFAULT_EMBEDDER, ImageEmbedder
from winescan.vision.preprocess import cutout, fit_on_square, label_region, reference_view

log = logging.getLogger("winescan.search.build_index")

MAX_REFERENCE_SIDE = 1024


def load_reference_view(path: Path, view: str = "full") -> Image.Image:
    with Image.open(path) as image:
        image.load()
        image = image.copy()
    image.thumbnail((MAX_REFERENCE_SIDE, MAX_REFERENCE_SIDE))
    return reference_view(image, view)


def load_reference_views(path: Path, view: str, yaws: tuple[float, ...]) -> list[Image.Image]:
    """Виды эталона под разными поворотами бутылки вокруг оси (цилиндрическая модель, vision.cylinder).

    Мультиракурсная галерея: на синтетике с поворотами (synth_v2) визуальный top-5 падает до 0,76,
    а фронтальный эталон не похож на повёрнутую этикетку. Невысокие упаковки не поворачиваются.
    """
    if yaws == (0.0,):
        return [load_reference_view(path, view)]
    with Image.open(path) as image:
        image.load()
        image = image.copy()
    image.thumbnail((MAX_REFERENCE_SIDE, MAX_REFERENCE_SIDE))
    package = cutout(image)
    tall = package.height / max(package.width, 1) >= 1.8
    views = []
    for yaw in yaws if tall else (0.0,):
        rotated = rotate_cylinder(package, yaw_deg=yaw) if yaw else package
        views.append(fit_on_square(label_region(rotated) if view == "label" else rotated))
    return views


def build(
    model_id: str,
    name: str | None,
    batch_size: int,
    device: str | None,
    view: str = "full",
    yaws: tuple[float, ...] = (0.0,),
) -> Path:
    paths = get_paths()
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    wines = catalog.dropna(subset=["image_file"]).sort_values("slug").reset_index(drop=True)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        per_wine = list(pool.map(lambda f: load_reference_views(paths.uploads_dir / f, view, yaws), wines["image_file"]))
    views = [image for images in per_wine for image in images]
    slugs = [slug for slug, images in zip(wines["slug"], per_wine) for _ in images]
    prepared = time.monotonic()

    embedder = ImageEmbedder(model_id, device=device)
    vectors = embedder.embed(views, batch_size=batch_size)
    embedded = time.monotonic()

    index = VectorIndex(
        slugs=slugs,
        vectors=vectors,
        meta={
            "model_id": model_id,
            "view_name": view,
            "view": "reference_view: cutout -> " + ("этикетка -> " if view == "label" else "") + "белый квадрат",
            "yaws": list(yaws),
            "vectors": len(slugs),
            "wines": len(wines),
            "dim": int(vectors.shape[1]),
            "device": embedder.device,
            "prepare_seconds": round(prepared - started, 1),
            "embed_seconds": round(embedded - prepared, 1),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    suffix = ("" if view == "full" else f"__{view}") + ("" if yaws == (0.0,) else "__yaw" + "_".join(f"{y:g}" for y in yaws))
    out_dir = paths.artifacts_dir / "index" / (name or model_id.split("/")[-1] + suffix)
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
    parser.add_argument("--view", choices=("full", "label"), default="full", help="вся упаковка или зона этикетки")
    parser.add_argument("--yaws", default="0", help="углы поворота бутылки через запятую, например -30,-15,0,15,30")
    args = parser.parse_args(argv)
    setup_logging()
    yaws = tuple(float(v) for v in args.yaws.split(","))
    build(args.model, args.name, args.batch_size, args.device, args.view, yaws)


if __name__ == "__main__":
    main()
