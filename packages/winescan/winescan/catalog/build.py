"""Сборка чистого каталога: CSV-выгрузка + uploads Strapi -> artifacts/catalog/.

Запуск: ``python -m winescan.catalog.build [--workers N] [--no-review]``

Результаты:
    catalog.jsonl             одна карточка вина на строку (формат для API и индексации)
    catalog.parquet           та же таблица плоско, со служебными колонками
    photo_matching.csv        как и почему каждому вину выбран файл эталона
    near_duplicate_images.csv группы вин с почти одинаковыми эталонами
    uploads_index.csv         все файлы uploads с ключами имён
    candidate_images.csv      техописание всех файлов-кандидатов и файлов из ручных решений
    build_report.md           сводка сборки
    review/*.jpg              контактные листы для ручной проверки
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from winescan.catalog.attributes import attributes_dict
from winescan.catalog.images import inspect_image, inspect_images, packshot_score
from winescan.catalog.loader import load_catalog_csv
from winescan.catalog.matching import (
    Status,
    apply_overrides,
    find_candidates,
    index_uploads,
    load_overrides,
    orphan_uploads_near,
    originals_by_key,
    resolve_matches,
    suggest_uploads,
)
from winescan.catalog.report import render_report
from winescan.catalog.review import Cell, SheetRow, render_sheets
from winescan.config import Paths, get_paths
from winescan.logging_setup import setup_logging

log = logging.getLogger("winescan.catalog.build")

NEAR_DUPLICATE_PHASH_DISTANCE = 4
ORPHAN_EXTENSIONS = frozenset({".webp", ".png", ".jpg", ".jpeg"})


def _clean(value):
    """NaN/numpy -> JSON-совместимые значения."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        return _clean(value.item())
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _int_or_none(value) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _image_records(images: pd.DataFrame) -> dict[str, dict]:
    return {row["filename"]: _clean(row) for row in images.to_dict("records")}


def phash_neighbours(catalog: pd.DataFrame, threshold: int) -> tuple[pd.DataFrame, dict[str, int]]:
    """Группы вин с близкими эталонами и распределение расстояния до ближайшего чужого эталона."""
    with_hash = catalog[catalog["image_phash"].notna()].reset_index(drop=True)
    hashes = np.array([int(h, 16) for h in with_hash["image_phash"]], dtype=np.uint64)
    distances = np.bitwise_count(hashes[:, None] ^ hashes[None, :]).astype(np.int16)
    np.fill_diagonal(distances, 64)
    nearest = distances.min(axis=1)
    buckets = {"0": 0, "1–4": 0, "5–8": 0, "9–12": 0, "> 12": 0}
    for d in nearest:
        key = "0" if d == 0 else "1–4" if d <= 4 else "5–8" if d <= 8 else "9–12" if d <= 12 else "> 12"
        buckets[key] += 1

    parent = list(range(len(with_hash)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in np.argwhere(np.triu(distances <= threshold, k=1)):
        parent[find(int(i))] = find(int(j))

    roots = pd.Series([find(i) for i in range(len(with_hash))])
    sizes = roots.map(roots.value_counts())
    grouped = with_hash.loc[sizes > 1, ["slug", "name", "winery", "photo_name", "image_file", "image_phash"]].copy()
    grouped["group_id"] = roots[sizes > 1].rank(method="dense").astype(int).to_numpy()
    grouped["nearest_distance"] = nearest[sizes > 1]
    return grouped.sort_values(["group_id", "slug"]).reset_index(drop=True), buckets


def _card(row: pd.Series) -> dict:
    return _clean(
        {
            "slug": row["slug"],
            "name": row["name"],
            "winery": row["winery"],
            "region": row["region"],
            "category": row["category"],
            "color": row["color"],
            "grapes": list(row["grapes_list"]),
            "description": row["description"],
            "attributes": {
                "year": _int_or_none(row["year"]),
                "sweetness": row["sweetness"],
                "sparkling": bool(row["sparkling"]),
                "volume_l": row["volume_l"],
            },
            "image": {
                "file": row["image_file"],
                "status": row["image_status"],
                "width": _int_or_none(row["image_width"]),
                "height": _int_or_none(row["image_height"]),
                "source_photo_name": row["photo_name"],
            },
        }
    )


def _review_rows(catalog: pd.DataFrame, statuses: set[str], uploads_dir: Path, mtimes: dict[str, float]) -> list[SheetRow]:
    rows = []
    for r in catalog[catalog["image_status"].isin(statuses)].itertuples():
        # pandas 3 хранит пропуск в строковой колонке как NaN, а не None
        has_file = isinstance(r.image_file, str)
        # у ручного решения выбранный файл может совпасть с одной из альтернатив автоматики
        files = list(dict.fromkeys([r.image_file, *r.image_alternatives])) if has_file else list(r.suggestions)
        cells = [
            Cell(
                uploads_dir / f,
                f"{f}\nзагружен {datetime.fromtimestamp(mtimes[f]):%Y-%m-%d %H:%M}",
                highlight=(f == r.image_file),
            )
            for f in files
        ]
        rows.append(SheetRow(f"[{r.image_status}] {r.slug} | {r.winery} | {r.name}", cells))
    return rows


def build(paths: Paths, workers: int | None = None, review: bool = True) -> Path:
    started = time.monotonic()
    out_dir = paths.artifacts_dir / "catalog"
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog, load_stats = load_catalog_csv(paths.catalog_csv)
    log.info("CSV: %s строк -> %s вин", load_stats.rows_raw, load_stats.unique_slugs)

    uploads = index_uploads(paths.uploads_dir)
    known_files = set(uploads["filename"])
    by_key = originals_by_key(uploads)
    catalog["candidates"] = find_candidates(catalog["photo_name"], uploads)
    candidate_files = sorted({f for files in catalog["candidates"] for f in files})
    overrides = load_overrides(paths.photo_overrides_csv)
    # файлы из ручных решений (например, «сироты») тоже описываем, иначе у карточки не будет размеров и хешей
    override_files = {f for f in overrides["image_file"] if f} & known_files
    log.info(
        "uploads: %s файлов, кандидатов: %s, файлов из overrides: %s",
        len(uploads), len(candidate_files), len(override_files),
    )  # fmt: skip

    inspected_files = sorted(set(candidate_files) | override_files)
    candidate_images = inspect_images([paths.uploads_dir / f for f in inspected_files], workers=workers)
    images = _image_records(candidate_images)
    mtimes = dict(zip(uploads["filename"], uploads["mtime"]))

    matches = resolve_matches(catalog, mtimes, images)
    matches = apply_overrides(matches, overrides, known_files=known_files)
    catalog = catalog.merge(matches, on="slug", how="left", validate="one_to_one")

    orphans = uploads[
        ~uploads["is_format_variant"]
        & uploads["ext"].isin(ORPHAN_EXTENSIONS)
        & ~uploads["filename"].isin(set(candidate_files))
    ]
    winery_mtimes = (
        catalog.dropna(subset=["image_file"])
        .groupby("winery")["image_file"]
        .apply(lambda files: [mtimes[f] for f in files])
        .to_dict()
    )

    def suggestions_for(row) -> list[str]:
        """Для «missing»: похожие имена + студийные «сироты» из пачки загрузки винодельни."""
        if row.image_status != Status.MISSING:
            return []
        near = orphan_uploads_near(winery_mtimes.get(row.winery, []), orphans)
        packshots = [f for f in near if packshot_score(inspect_image(paths.uploads_dir / f)) >= 2][:6]
        return list(dict.fromkeys(suggest_uploads(row.photo_name, by_key) + packshots))

    catalog["suggestions"] = [suggestions_for(row) for row in catalog.itertuples(index=False)]

    attributes = pd.DataFrame([attributes_dict(n, s) for n, s in zip(catalog["name"], catalog["slug"])])
    attributes["year"] = attributes["year"].astype("Int64")
    catalog = pd.concat([catalog, attributes], axis=1)

    meta_columns = ["width", "height", "format", "mode", "transparent_share", "sha256", "phash", "error"]
    meta = candidate_images.set_index("filename")[meta_columns].add_prefix("image_")
    catalog = catalog.join(meta, on="image_file")
    catalog["image_upload_time"] = catalog["image_file"].map(
        lambda f: datetime.fromtimestamp(mtimes[f]).isoformat(timespec="seconds") if isinstance(f, str) else None
    )

    near_duplicates, min_distance_counts = phash_neighbours(catalog, NEAR_DUPLICATE_PHASH_DISTANCE)

    catalog.to_parquet(out_dir / "catalog.parquet", index=False)
    with (out_dir / "catalog.jsonl").open("w", encoding="utf-8") as fh:
        for _, row in catalog.iterrows():
            fh.write(json.dumps(_card(row), ensure_ascii=False) + "\n")

    matching = catalog[["slug", "winery", "name", "photo_name", "image_status", "image_file", "image_note"]].copy()
    matching["n_candidates"] = catalog["candidates"].map(len)
    for column in ("candidates", "image_alternatives", "suggestions"):
        matching[column] = catalog[column].map(lambda files: ";".join(files))
    matching.to_csv(out_dir / "photo_matching.csv", index=False)
    near_duplicates.to_csv(out_dir / "near_duplicate_images.csv", index=False)
    uploads.to_csv(out_dir / "uploads_index.csv", index=False)
    candidate_images.to_csv(out_dir / "candidate_images.csv", index=False)

    sheets: dict[str, list[Path]] = {}
    if review:
        review_dir = out_dir / "review"
        sheets["ambiguous"] = render_sheets(
            _review_rows(catalog, {Status.AMBIGUOUS}, paths.uploads_dir, mtimes), review_dir, "ambiguous"
        )
        sheets["auto_resolved"] = render_sheets(
            _review_rows(
                catalog, {Status.EXACT_NAME, Status.UPLOAD_TIME, Status.WINERY_BATCH}, paths.uploads_dir, mtimes
            ),
            review_dir,
            "auto_resolved",
        )
        sheets["manual"] = render_sheets(
            _review_rows(catalog, {Status.MANUAL}, paths.uploads_dir, mtimes), review_dir, "manual"
        )
        sheets["missing"] = render_sheets(
            _review_rows(catalog, {Status.MISSING}, paths.uploads_dir, mtimes), review_dir, "missing"
        )
        near_rows = [
            SheetRow(
                f"группа {group_id}: {len(group)} вин",
                [Cell(paths.uploads_dir / r.image_file, f"{r.slug}\n{r.winery}") for r in group.itertuples()],
            )
            for group_id, group in near_duplicates.groupby("group_id")
        ]
        sheets["near_duplicates"] = render_sheets(near_rows, review_dir, "near_duplicates")

    report = render_report(
        paths={
            "CSV": paths.catalog_csv,
            "uploads": paths.uploads_dir,
            "overrides": paths.photo_overrides_csv,
            "результаты": out_dir,
        },
        load_stats=load_stats,
        uploads=uploads,
        catalog=catalog,
        candidate_images=candidate_images,
        near_duplicates=near_duplicates,
        min_distance_counts=min_distance_counts,
        near_duplicate_threshold=NEAR_DUPLICATE_PHASH_DISTANCE,
        review_sheets=sheets,
        elapsed_seconds=time.monotonic() - started,
    )
    report_path = out_dir / "build_report.md"
    report_path.write_text(report, encoding="utf-8")
    log.info("готово за %.0f с: %s", time.monotonic() - started, report_path)
    return report_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Собрать чистый каталог вин с эталонными фото")
    parser.add_argument("--workers", type=int, default=None, help="процессов для чтения изображений")
    parser.add_argument("--no-review", action="store_true", help="не рисовать контактные листы")
    args = parser.parse_args(argv)
    setup_logging()
    build(get_paths(), workers=args.workers, review=not args.no_review)


if __name__ == "__main__":
    main()
