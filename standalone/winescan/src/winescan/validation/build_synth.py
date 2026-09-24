"""Сборка синтетической валидационной выборки: artifacts/validation/<name>/.

Запуск: ``python -m winescan.validation.build_synth --name synth_v1 --seed 1 [--per-wine 1] [--limit N]``

Требует собранный каталог (``python -m winescan.catalog.build``).

Результаты:
    images/<query_id>.jpg  кадры
    manifest.csv           query_id, image_path, slug, bbox целевой упаковки, соседи, фон,
                           флаги сложных случаев (in_phash_group, shares_image)
    queries.tsv            манифест в формате participant_test.sh (query_id<TAB>image_path)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd
from PIL import Image

from winescan.config import get_paths
from winescan.logging_setup import setup_logging
from winescan.validation.synth import SYNTH_PRESETS, SynthConfig, render_sample

log = logging.getLogger("winescan.validation.build_synth")

BACKGROUND_EXTENSIONS = frozenset({".webp", ".jpg", ".jpeg", ".png"})
BACKGROUND_MIN_SIZE = (800, 600)
MAX_SOURCE_SIDE = 2000  # эталоны бывают до 9506 px; для кадра 1080×1440 столько не нужно


@dataclass(frozen=True)
class RenderTask:
    query_id: str
    slug: str
    target_file: str
    neighbour_files: tuple[str, ...]
    background_file: str | None
    seed: int
    out_path: str
    uploads_dir: str
    preset: str = "v1"


def select_backgrounds(uploads: pd.DataFrame, linked_files: set[str], uploads_dir: Path) -> list[str]:
    """Непривязанные к каталогу непрозрачные фото не меньше 800×600: винодельни, события, полки."""
    rows = uploads[
        ~uploads["is_format_variant"]
        & uploads["ext"].isin(BACKGROUND_EXTENSIONS)
        & ~uploads["filename"].isin(linked_files)
    ]
    selected = []
    for filename in rows["filename"]:
        try:
            with Image.open(uploads_dir / filename) as image:
                big = image.width >= BACKGROUND_MIN_SIZE[0] and image.height >= BACKGROUND_MIN_SIZE[1]
                if big and image.mode not in ("RGBA", "LA", "P", "PA"):
                    selected.append(filename)
        except Exception:  # битые файлы в медиатеке не нужны
            continue
    return sorted(selected)


def plan_tasks(
    catalog: pd.DataFrame,
    backgrounds: list[str],
    out_dir: Path,
    uploads_dir: Path,
    seed: int,
    per_wine: int,
    limit: int | None,
    config: SynthConfig,
) -> list[RenderTask]:
    rng = random.Random(seed)
    wines = catalog[catalog["image_file"].notna()].reset_index(drop=True)
    if limit:
        wines = wines.sample(n=min(limit, len(wines)), random_state=seed).reset_index(drop=True)
    files_by_winery = catalog.dropna(subset=["image_file"]).groupby("winery")["image_file"].apply(list).to_dict()
    all_files = catalog["image_file"].dropna().tolist()

    tasks = []
    for wine in wines.itertuples(index=False):
        for _ in range(per_wine):
            same_winery = rng.random() < config.same_winery_neighbours_prob
            pool = files_by_winery.get(wine.winery, []) if same_winery else all_files
            pool = [f for f in pool if f != wine.image_file] or [f for f in all_files if f != wine.image_file]
            count = min(rng.randint(0, config.max_neighbours), len(pool))
            query_id = f"s-{len(tasks) + 1:06d}"
            tasks.append(
                RenderTask(
                    query_id=query_id,
                    slug=wine.slug,
                    target_file=wine.image_file,
                    neighbour_files=tuple(rng.sample(pool, count)),
                    background_file=rng.choice(backgrounds) if backgrounds else None,
                    seed=rng.randrange(2**31),
                    out_path=str(out_dir / "images" / f"{query_id}.jpg"),
                    uploads_dir=str(uploads_dir),
                )
            )
    return tasks


def _load(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image.draft("RGB", (MAX_SOURCE_SIDE, MAX_SOURCE_SIDE))
        image.load()
        image = image.copy()
    image.thumbnail((MAX_SOURCE_SIDE, MAX_SOURCE_SIDE))
    return image


def _render(task: RenderTask) -> dict:
    uploads_dir = Path(task.uploads_dir)
    target = _load(uploads_dir / task.target_file)
    neighbours = [_load(uploads_dir / f) for f in task.neighbour_files]
    background = _load(uploads_dir / task.background_file) if task.background_file else None
    image, box = render_sample(target, neighbours, background, random.Random(task.seed), SYNTH_PRESETS[task.preset])
    image.save(task.out_path, quality=92)
    return {"query_id": task.query_id, "bbox": ",".join(str(v) for v in box)}


def build(name: str, seed: int, per_wine: int, limit: int | None, workers: int | None, preset: str = "v1") -> Path:
    started = time.monotonic()
    paths = get_paths()
    catalog_dir = paths.artifacts_dir / "catalog"
    catalog = pd.read_parquet(catalog_dir / "catalog.parquet")
    uploads = pd.read_csv(catalog_dir / "uploads_index.csv")
    near_duplicates = pd.read_csv(catalog_dir / "near_duplicate_images.csv")

    out_dir = paths.artifacts_dir / "validation" / name
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    for stale in (out_dir / "images").glob("*.jpg"):
        stale.unlink()

    backgrounds = select_backgrounds(uploads, set(catalog["image_file"].dropna()), paths.uploads_dir)
    log.info("фоновых фото: %s", len(backgrounds))
    config = SYNTH_PRESETS[preset]
    tasks = plan_tasks(catalog, backgrounds, out_dir, paths.uploads_dir, seed, per_wine, limit, config)
    tasks = [replace(task, preset=preset) for task in tasks]

    workers = workers or min(32, os.cpu_count() or 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        boxes = {row["query_id"]: row["bbox"] for row in pool.map(_render, tasks, chunksize=4)}

    hard_slugs = set(near_duplicates["slug"])
    shared_sha = catalog["image_sha256"][catalog["image_sha256"].duplicated(keep=False)]
    shared_slugs = set(catalog.loc[catalog["image_sha256"].isin(shared_sha), "slug"])
    winery_of = dict(zip(catalog["slug"], catalog["winery"]))
    manifest = pd.DataFrame(
        [
            {
                "query_id": t.query_id,
                "image_path": f"{t.query_id}.jpg",
                "slug": t.slug,
                "winery": winery_of[t.slug],
                "bbox": boxes[t.query_id],
                "target_file": t.target_file,
                "neighbours": ";".join(t.neighbour_files),
                "background": t.background_file or "",
                "in_phash_group": t.slug in hard_slugs,
                "shares_image": t.slug in shared_slugs,
            }
            for t in tasks
        ]
    )
    manifest.to_csv(out_dir / "manifest.csv", index=False)
    manifest[["query_id", "image_path"]].to_csv(out_dir / "queries.tsv", sep="\t", index=False)
    (out_dir / "build_info.json").write_text(
        json.dumps(
            {"name": name, "preset": preset, "seed": seed, "per_wine": per_wine, "limit": limit, "samples": len(manifest),
             "backgrounds": len(backgrounds), "config": asdict(config)},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )  # fmt: skip
    log.info("готово за %.0f с: %s кадров -> %s", time.monotonic() - started, len(manifest), out_dir)
    return out_dir


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Собрать синтетическую валидационную выборку")
    parser.add_argument("--name", default="synth_v1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--per-wine", type=int, default=1, help="кадров на вино")
    parser.add_argument("--limit", type=int, default=None, help="взять случайные N вин")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--preset", choices=sorted(SYNTH_PRESETS), default="v1", help="v2 — цилиндр, тесные соседи, ценники")
    args = parser.parse_args(argv)
    setup_logging()
    build(args.name, args.seed, args.per_wine, args.limit, args.workers, args.preset)


if __name__ == "__main__":
    main()
