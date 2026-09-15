"""Прогон поиска на валидационной выборке: artifacts/eval/<run>/.

Запуск::

    python -m winescan.eval.run --split synth_v1 --index siglip2-base-patch16-224 --crop gt
    python -m winescan.eval.run --split synth_v1 --index siglip2-so400m-patch14-384,siglip2-so400m-patch14-384__label \
        --index-weights 0.5,0.5 --crop gt
    python -m winescan.eval.run --split public --index siglip2-so400m-patch14-384 --crop detector --ocr

``--index`` — одна или несколько папок в artifacts/index; скоры вин складываются с весами
``--index-weights``. Вид запроса (вся упаковка / этикетка) берётся из meta индекса.
``--crop``: ``gt`` — рамка из манифеста синтетики (верхняя граница для слоя 1),
``detector`` — рамка OWLv2, ``none`` — весь кадр.
``--ocr`` — дополнительно прочитать текст кропа и сохранить в predictions.csv; вес текста
подбирается отдельно: ``python -m winescan.eval.rerank_sweep <run>``.
Сплит ``public`` — 3 публичных фото с неофициальной разметкой configs/eval_public_labels.csv;
пустой expected_slug означает «вина нет в каталоге».
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageOps

from winescan.config import PROJECT_ROOT, get_paths
from winescan.eval.metrics import best_threshold, retrieval_summary
from winescan.logging_setup import setup_logging
from winescan.search.index import VectorIndex, top_k
from winescan.vision.embedder import ImageEmbedder
from winescan.vision.preprocess import crop_box, query_view

log = logging.getLogger("winescan.eval.run")

TOP_K = 10
MAX_QUERY_SIDE = 1600
SUBSETS = ("all", "in_phash_group", "not_in_phash_group", "shares_image")


def load_split(name: str) -> tuple[pd.DataFrame, Path]:
    paths = get_paths()
    if name == "public":
        labels = pd.read_csv(PROJECT_ROOT / "configs" / "eval_public_labels.csv", dtype=str, keep_default_na=False)
        manifest = labels.rename(columns={"expected_slug": "slug"}).assign(bbox="", in_phash_group=False, shares_image=False)
        return manifest, paths.data_dir / "eval" / "queries"
    split_dir = paths.artifacts_dir / "validation" / name
    manifest = pd.read_csv(split_dir / "manifest.csv", dtype=str, keep_default_na=False)
    return manifest, split_dir / "images"


def _load_query(path: Path) -> tuple[Image.Image, float]:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
    original_width = image.width
    image.thumbnail((MAX_QUERY_SIDE, MAX_QUERY_SIDE))
    return image, image.width / original_width


def summarize(predictions: pd.DataFrame, rank_column: str = "rank", margin_column: str = "margin") -> dict:
    """Метрики по подвыборкам для запросов с вином из каталога."""
    in_catalog = predictions[predictions["expected_slug"] != ""]
    parts = {
        "all": in_catalog,
        "in_phash_group": in_catalog[in_catalog["in_phash_group"]],
        "not_in_phash_group": in_catalog[~in_catalog["in_phash_group"]],
        "shares_image": in_catalog[in_catalog["shares_image"]],
    }
    result = {}
    for name, frame in parts.items():
        ranks = [None if pd.isna(rank) else int(rank) for rank in frame[rank_column]]
        summary = retrieval_summary(ranks, frame[margin_column].to_numpy())
        top1 = np.array([rank == 1 for rank in ranks], bool)
        summary["f1_at_1_best_by_score"] = best_threshold(top1, frame["score_top1"].to_numpy())
        result[name] = summary
    return result


def markdown_table(metrics: dict) -> str:
    lines = ["| подвыборка | n | top-1 | top-5 | F1@1 (лучший порог по отрыву) |", "|---|---|---|---|---|"]
    for part in SUBSETS:
        s = metrics[part]
        lines.append(f"| {part} | {s['queries']} | {s['top1_accuracy']:.3f} | {s['top5_accuracy']:.3f} | "
                     f"{s['f1_at_1_best']['f1']:.3f} |")  # fmt: skip
    return "\n".join(lines)


class MultiIndexSearcher:
    """Сумма скоров вин по нескольким индексам (модели и виды могут различаться)."""

    def __init__(self, index_names: list[str], weights: list[float], device: str | None):
        paths = get_paths()
        self.indexes = [VectorIndex.load(paths.artifacts_dir / "index" / name) for name in index_names]
        self.weights = weights
        self.wine_slugs = self.indexes[0].wine_slugs
        if any(index.wine_slugs != self.wine_slugs for index in self.indexes):
            raise ValueError("индексы построены по разным наборам вин — пересоберите их")
        self.embedders: dict[str, ImageEmbedder] = {}
        for index in self.indexes:
            model_id = index.meta["model_id"]
            if model_id not in self.embedders:
                self.embedders[model_id] = ImageEmbedder(model_id, device=device)

    @property
    def device(self) -> str:
        return next(iter(self.embedders.values())).device

    def search(self, images: list[Image.Image], boxes: list, k: int) -> tuple[list[list[str]], np.ndarray]:
        total = np.zeros((len(images), len(self.wine_slugs)), dtype=np.float32)
        for index, weight in zip(self.indexes, self.weights):
            view = index.meta.get("view_name", "full")
            views = [query_view(image, box, view) for image, box in zip(images, boxes)]
            vectors = self.embedders[index.meta["model_id"]].embed(views, batch_size=len(views))
            total += weight * index.wine_scores(vectors)
        return top_k(total, self.wine_slugs, k)


def run(
    split: str,
    index_names: list[str],
    weights: list[float],
    crop: str,
    limit: int | None,
    device: str | None,
    batch_size: int,
    ocr: bool,
) -> Path:
    paths = get_paths()
    manifest, images_dir = load_split(split)
    for flag in ("in_phash_group", "shares_image"):
        manifest[flag] = manifest[flag].astype(str).eq("True")
    if limit:
        manifest = manifest.sample(n=min(limit, len(manifest)), random_state=0)

    searcher = MultiIndexSearcher(index_names, weights, device)
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    sha_of = dict(zip(catalog["slug"], catalog["image_sha256"]))
    detector = reader = None
    if crop == "detector":
        from winescan.vision.detector import PackageDetector, choose_main_package

        detector = PackageDetector(device=device)
    if ocr:
        from winescan.vision.ocr import LabelReader

        reader = LabelReader(gpu=searcher.device.startswith("cuda"))

    records = []
    rows = list(manifest.itertuples(index=False))
    started = time.monotonic()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        prepared = []
        for row in batch:
            image, scale = _load_query(images_dir / row.image_path)
            box, detector_score = None, float("nan")
            began = time.perf_counter()
            if crop == "gt" and row.bbox:
                box = tuple(float(v) * scale for v in row.bbox.split(","))
            elif detector is not None:
                main = choose_main_package(detector.detect(image), image.size)
                if main is not None:
                    box, detector_score = main.box, main.score
            detect_ms = (time.perf_counter() - began) * 1000
            ocr_text, ocr_ms = "", 0.0
            if reader is not None:
                began = time.perf_counter()
                ocr_text = reader.read(crop_box(image, box) if box else image)
                ocr_ms = (time.perf_counter() - began) * 1000
            prepared.append((image, box, detector_score, detect_ms, ocr_text, ocr_ms))

        began = time.perf_counter()
        top_slugs, top_scores = searcher.search([p[0] for p in prepared], [p[1] for p in prepared], TOP_K)
        embed_ms = (time.perf_counter() - began) * 1000 / len(prepared)

        for row, (_, box, detector_score, detect_ms, ocr_text, ocr_ms), slugs, scores in zip(
            batch, prepared, top_slugs, top_scores
        ):
            expected = row.slug or None
            rank = slugs.index(expected) + 1 if expected in slugs else None
            records.append(
                {
                    "query_id": row.query_id,
                    "expected_slug": expected or "",
                    "predicted_slug": slugs[0],
                    "rank": rank,
                    "correct_top1": rank == 1,
                    "same_image_top1": expected is not None and sha_of.get(slugs[0]) == sha_of.get(expected),
                    "score_top1": float(scores[0]),
                    "margin": float(scores[0] - scores[1]),
                    "top_slugs": ";".join(slugs),
                    "top_scores": ";".join(f"{s:.5f}" for s in scores),
                    "box": "" if box is None else ",".join(f"{v:.0f}" for v in box),
                    "detector_score": detector_score,
                    "ocr_text": ocr_text,
                    "detect_ms": round(detect_ms, 1),
                    "ocr_ms": round(ocr_ms, 1),
                    "embed_ms": round(embed_ms, 1),
                    "in_phash_group": row.in_phash_group,
                    "shares_image": row.shares_image,
                }
            )
        log.info("%s / %s", min(start + batch_size, len(rows)), len(rows))

    predictions = pd.DataFrame(records)
    out_of_catalog = predictions[predictions["expected_slug"] == ""]
    in_catalog = predictions[predictions["expected_slug"] != ""]
    metrics = {
        "split": split,
        "indexes": index_names,
        "index_weights": weights,
        "models": [index.meta["model_id"] for index in searcher.indexes],
        "crop": crop,
        "ocr": ocr,
        "queries": len(predictions),
        "seconds": round(time.monotonic() - started, 1),
        **summarize(predictions),
        "same_image_top1": float(in_catalog["same_image_top1"].mean()) if len(in_catalog) else 0.0,
        "latency_ms": {
            "detect_mean": float(predictions["detect_ms"].mean()),
            "ocr_mean": float(predictions["ocr_ms"].mean()),
            "embed_mean_in_batch": float(predictions["embed_ms"].mean()),
        },
        "out_of_catalog": out_of_catalog[["query_id", "predicted_slug", "score_top1", "margin"]].to_dict("records"),
    }
    weights_tag = "" if len(index_names) == 1 else "@" + ",".join(f"{w:g}" for w in weights)
    run_name = (f"{split}__{'+'.join(index_names)}{weights_tag}__{crop}" + ("__ocr" if ocr else "")
                + (f"__limit{limit}" if limit else ""))  # fmt: skip
    out_dir = paths.artifacts_dir / "eval" / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(out_dir / "predictions.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{run_name}: {len(in_catalog)} запросов из каталога, {len(out_of_catalog)} вне каталога")
    print(markdown_table(metrics))
    print(f"top-1 с тем же изображением, что у верного вина: {metrics['same_image_top1']:.3f}")
    latency = metrics["latency_ms"]
    print(f"задержка, мс: детекция {latency['detect_mean']:.0f}, OCR {latency['ocr_mean']:.0f}, "
          f"эмбеддинги {latency['embed_mean_in_batch']:.0f} (в батче)")  # fmt: skip
    return out_dir


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Прогон поиска на валидационной выборке")
    parser.add_argument("--split", required=True, help="synth_v1, public, ...")
    parser.add_argument("--index", required=True, help="папки в artifacts/index через запятую")
    parser.add_argument("--index-weights", default=None, help="веса индексов через запятую (по умолчанию поровну)")
    parser.add_argument("--crop", choices=("gt", "detector", "none"), default="detector")
    parser.add_argument("--ocr", action="store_true", help="читать текст этикетки для переранжирования")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    setup_logging()
    names = [name.strip() for name in args.index.split(",") if name.strip()]
    weights = [float(w) for w in args.index_weights.split(",")] if args.index_weights else [1 / len(names)] * len(names)
    if len(weights) != len(names):
        parser.error("число весов не совпадает с числом индексов")
    run(args.split, names, weights, args.crop, args.limit, args.device, args.batch_size, args.ocr)


if __name__ == "__main__":
    main()
