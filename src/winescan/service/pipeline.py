"""Пайплайн распознавания одного фото: детекция -> нормализация -> эмбеддинг -> поиск -> карточка."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from winescan.config import get_paths
from winescan.search.index import VectorIndex
from winescan.vision.preprocess import query_view

MAX_QUERY_SIDE = 1600


@dataclass(frozen=True)
class ScannerConfig:
    index_name: str = "siglip2-base-patch16-224"
    device: str | None = None
    use_detector: bool = True
    # отрыв top-1 от top-2, ниже которого отвечаем «не найдено» (None — отвечаем всегда)
    margin_threshold: float | None = None

    @classmethod
    def from_env(cls) -> ScannerConfig:
        threshold = os.environ.get("WINESCAN_MARGIN_THRESHOLD")
        return cls(
            index_name=os.environ.get("WINESCAN_INDEX", cls.index_name),
            device=os.environ.get("WINESCAN_DEVICE") or None,
            use_detector=os.environ.get("WINESCAN_USE_DETECTOR", "1") != "0",
            margin_threshold=float(threshold) if threshold else None,
        )


@dataclass
class ScanResult:
    status: str  # found | not_found
    slug: str | None
    card: dict | None
    top5: list[dict]
    confidence: dict
    box: list[float] | None
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "slug": self.slug,
            "card": self.card,
            "confidence": self.confidence,
            "top5": self.top5,
            "box": self.box,
            "timings_ms": self.timings_ms,
        }


def load_cards(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as fh:
        return {card["slug"]: card for card in map(json.loads, fh)}


class Scanner:
    def __init__(self, config: ScannerConfig = ScannerConfig()):
        from winescan.vision.embedder import ImageEmbedder

        paths = get_paths()
        self.config = config
        self.index = VectorIndex.load(paths.artifacts_dir / "index" / config.index_name)
        self.cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl")
        self.embedder = ImageEmbedder(self.index.meta["model_id"], device=config.device)
        self.detector = None
        if config.use_detector:
            from winescan.vision.detector import PackageDetector

            self.detector = PackageDetector(device=config.device)
        self._lock = threading.Lock()  # модели на одном GPU: запросы обрабатываются по очереди

    def warmup(self) -> None:
        self.scan(Image.new("RGB", (640, 960), (200, 200, 200)))

    def scan(self, image: Image.Image) -> ScanResult:
        from winescan.vision.detector import choose_main_package

        timings: dict[str, float] = {}
        began = time.perf_counter()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_QUERY_SIDE, MAX_QUERY_SIDE))
        timings["decode"] = (time.perf_counter() - began) * 1000

        with self._lock:
            box = None
            if self.detector is not None:
                step = time.perf_counter()
                main = choose_main_package(self.detector.detect(image), image.size)
                box = main.box if main is not None else None
                timings["detect"] = (time.perf_counter() - step) * 1000
            step = time.perf_counter()
            vector = self.embedder.embed([query_view(image, box)], batch_size=1)
            timings["embed"] = (time.perf_counter() - step) * 1000
            slugs, scores = self.index.search(vector, k=5)
        timings["total"] = (time.perf_counter() - began) * 1000

        slugs, scores = slugs[0], scores[0]
        margin = float(scores[0] - scores[1]) if len(scores) > 1 else float(scores[0])
        found = self.config.margin_threshold is None or margin >= self.config.margin_threshold
        return ScanResult(
            status="found" if found else "not_found",
            slug=slugs[0] if found else None,
            card=self.cards.get(slugs[0]) if found else None,
            top5=[{"slug": s, "score": round(float(v), 4), "name": self.cards.get(s, {}).get("name")}
                  for s, v in zip(slugs, scores)],  # fmt: skip
            confidence={
                "score_top1": round(float(scores[0]), 4),
                "margin_top1_top2": round(margin, 4),
                "margin_threshold": self.config.margin_threshold,
            },
            box=[round(v, 1) for v in box] if box is not None else None,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
        )

    def top1_slug(self, image: Image.Image) -> str:
        """Для скрипта оценки: всегда лучший кандидат, даже при низкой уверенности."""
        return self.scan(image).top5[0]["slug"]
