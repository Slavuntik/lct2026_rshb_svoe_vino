"""Прогон поиска на валидационной выборке: artifacts/eval/<run>/.

Запуск::

    python -m winescan.eval.run --split synth_v1 --index siglip2-base-patch16-224 --crop gt
    python -m winescan.eval.run --split public   --index siglip2-base-patch16-224 --crop detector --ocr

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
from winescan.search.index import VectorIndex
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


def run(split: str, index_name: str, crop: str, limit: int | None, device: str | None, batch_size: int, ocr: bool) -> Path:
    paths = get_paths()
    manifest, images_dir = load_split(split)
    for flag in ("in_phash_group", "shares_image"):
        manifest[flag] = manifest[flag].astype(str).eq("True")
    if limit:
        manifest = manifest.sample(n=min(limit, len(manifest)), random_state=0)

    index = VectorIndex.load(paths.artifacts_dir / "index" / index_name)
    catalog = pd.read_parquet(paths.artifacts_dir / "catalog" / "catalog.parquet")
    sha_of = dict(zip(catalog["slug"], catalog["image_sha256"]))
    embedder = ImageEmbedder(index.meta["model_id"], device=device)
    detector = reader = None
    if crop == "detector":
        from winescan.vision.detector import PackageDetector, choose_main_package

        detector = PackageDetector(device=device)
    if ocr:
        from winescan.vision.ocr import LabelReader

        reader = LabelReader(gpu=(device or embedder.device).startswith("cuda"))

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
            prepared.append((query_view(image, box), box, detector_score, detect_ms, ocr_text, ocr_ms))

        began = time.perf_counter()
        vectors = embedder.embed([item[0] for item in prepared], batch_size=len(prepared))
        embed_ms = (time.perf_counter() - began) * 1000 / len(prepared)
        top_slugs, top_scores = index.search(vectors, k=TOP_K)

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
    metrics = {
        "split": split,
        "index": index_name,
        "model_id": index.meta["model_id"],
        "crop": crop,
        "ocr": ocr,
        "queries": len(predictions),
        "seconds": round(time.monotonic() - started, 1),
        **summarize(predictions),
        "same_image_top1": float(predictions.loc[predictions["expected_slug"] != "", "same_image_top1"].mean() or 0),
        "latency_ms": {
            "detect_mean": float(predictions["detect_ms"].mean()),
            "ocr_mean": float(predictions["ocr_ms"].mean()),
            "embed_mean_in_batch": float(predictions["embed_ms"].mean()),
        },
        "out_of_catalog": out_of_catalog[["query_id", "predicted_slug", "score_top1", "margin"]].to_dict("records"),
    }
    run_name = f"{split}__{index_name}__{crop}" + ("__ocr" if ocr else "") + (f"__limit{limit}" if limit else "")
    out_dir = paths.artifacts_dir / "eval" / run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(out_dir / "predictions.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{run_name}: {len(predictions) - len(out_of_catalog)} запросов из каталога, {len(out_of_catalog)} вне каталога")
    print(markdown_table(metrics))
    print(f"top-1 с тем же изображением, что у верного вина: {metrics['same_image_top1']:.3f}")
    latency = metrics["latency_ms"]
    print(f"задержка, мс: детекция {latency['detect_mean']:.0f}, OCR {latency['ocr_mean']:.0f}, "
          f"эмбеддинг {latency['embed_mean_in_batch']:.0f} (в батче)")  # fmt: skip
    return out_dir


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Прогон поиска на валидационной выборке")
    parser.add_argument("--split", required=True, help="synth_v1, public, ...")
    parser.add_argument("--index", required=True, help="папка в artifacts/index")
    parser.add_argument("--crop", choices=("gt", "detector", "none"), default="detector")
    parser.add_argument("--ocr", action="store_true", help="читать текст этикетки для переранжирования")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(args.split, args.index, args.crop, args.limit, args.device, args.batch_size, args.ocr)


if __name__ == "__main__":
    main()
