"""Пайплайн распознавания одного фото.

детекция упаковки -> нормализация -> эмбеддинги (несколько индексов) -> top-K
-> SIFT по top-N -> (OCR) -> слияние скоров -> решение -> карточка.
Все параметры — ScannerConfig, переопределяются переменными окружения (README.md).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from winescan.config import get_paths
from winescan.search.rerank import decide, fuse
from winescan.vision.preprocess import crop_box, cutout

MAX_QUERY_SIDE = 1600
SEARCH_TOP_K = 10


def _env_list(name: str, default: tuple, cast=str) -> tuple:
    value = os.environ.get(name)
    return tuple(cast(v.strip()) for v in value.split(",") if v.strip()) if value else default


def _env_float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


@dataclass(frozen=True)
class ScannerConfig:
    indexes: tuple[str, ...] = ("siglip2-so400m-patch14-384", "siglip2-so400m-patch14-384__label")
    index_weights: tuple[float, ...] = (0.5, 0.5)
    device: str | None = None
    use_detector: bool = True
    local_top: int = 5
    local_weight: float = 0.0
    use_ocr: bool = False
    text_weight: float = 0.0
    min_visual_score: float | None = None
    min_margin: float | None = None

    @classmethod
    def from_env(cls) -> ScannerConfig:
        base = cls()
        return cls(
            indexes=_env_list("WINESCAN_INDEXES", base.indexes),
            index_weights=_env_list("WINESCAN_INDEX_WEIGHTS", base.index_weights, float),
            device=os.environ.get("WINESCAN_DEVICE") or None,
            use_detector=os.environ.get("WINESCAN_USE_DETECTOR", "1") != "0",
            local_top=int(os.environ.get("WINESCAN_LOCAL_TOP", base.local_top)),
            local_weight=_env_float("WINESCAN_LOCAL_WEIGHT", base.local_weight),
            use_ocr=os.environ.get("WINESCAN_USE_OCR", "0") == "1",
            text_weight=_env_float("WINESCAN_TEXT_WEIGHT", base.text_weight),
            min_visual_score=_env_float("WINESCAN_MIN_VISUAL_SCORE", base.min_visual_score),
            min_margin=_env_float("WINESCAN_MIN_MARGIN", base.min_margin),
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
        from winescan.search.multi import MultiIndexSearcher

        paths = get_paths()
        self.config = config
        self.uploads_dir = paths.uploads_dir
        self.cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl")
        self.searcher = MultiIndexSearcher(list(config.indexes), list(config.index_weights), config.device)
        self.detector = self.reader = None
        if config.use_detector:
            from winescan.vision.detector import PackageDetector

            self.detector = PackageDetector(device=config.device)
        if config.use_ocr and config.text_weight > 0:
            from winescan.vision.ocr import LabelReader

            self.reader = LabelReader(gpu=self.searcher.device.startswith("cuda"))
        self._reference_features = lru_cache(maxsize=4096)(self._load_reference_features)
        self._lock = threading.Lock()  # модели на одном GPU: запросы обрабатываются по очереди

    def _load_reference_features(self, slug: str):
        from winescan.search.local_match import extract

        with Image.open(self.uploads_dir / self.cards[slug]["image"]["file"]) as image:
            image.load()
            image = image.copy()
        image.thumbnail((1024, 1024))
        return extract(cutout(image).convert("RGB"))

    def warmup(self) -> None:
        self.scan(Image.new("RGB", (640, 960), (200, 200, 200)))

    def scan(self, image: Image.Image) -> ScanResult:
        from winescan.search.local_match import extract, inliers
        from winescan.search.text_match import LabelText, text_score
        from winescan.vision.detector import choose_main_package

        config = self.config
        timings: dict[str, float] = {}
        began = time.perf_counter()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_QUERY_SIDE, MAX_QUERY_SIDE))

        with self._lock:
            box = None
            if self.detector is not None:
                step = time.perf_counter()
                main = choose_main_package(self.detector.detect(image), image.size)
                box = main.box if main is not None else None
                timings["detect"] = (time.perf_counter() - step) * 1000
            step = time.perf_counter()
            slugs, scores = self.searcher.search([image], [box], SEARCH_TOP_K)
            slugs, scores = slugs[0], [float(s) for s in scores[0]]
            timings["embed_search"] = (time.perf_counter() - step) * 1000
            package = crop_box(image, box) if box else image

            local = None
            if config.local_weight > 0 and config.local_top > 0:
                step = time.perf_counter()
                query_features = extract(package)
                local = {s: inliers(query_features, self._reference_features(s)) for s in slugs[: config.local_top]
                         if self.cards.get(s, {}).get("image", {}).get("file")}  # fmt: skip
                timings["local_match"] = (time.perf_counter() - step) * 1000

            texts, ocr_text = None, None
            if self.reader is not None:
                step = time.perf_counter()
                ocr_text = self.reader.read(package)
                label = LabelText.from_ocr(ocr_text)
                texts = {s: text_score(self.cards[s], label) for s in slugs if s in self.cards}
                timings["ocr"] = (time.perf_counter() - step) * 1000

        candidates = fuse(slugs, scores, local, texts, config.local_weight, config.text_weight)
        decision = decide(candidates, config.min_visual_score, config.min_margin)
        timings["total"] = (time.perf_counter() - began) * 1000
        best = candidates[0]
        found = decision.status == "found"
        return ScanResult(
            status=decision.status,
            slug=best.slug if found else None,
            card=self.cards.get(best.slug) if found else None,
            top5=[
                {"slug": c.slug, "name": self.cards.get(c.slug, {}).get("name"), "score": round(c.score, 4),
                 "visual_score": round(c.visual_score, 4), "local_inliers": c.local_inliers,
                 "text_score": round(c.text_score, 3)}
                for c in candidates[:5]
            ],  # fmt: skip
            confidence={
                "score_top1": round(best.score, 4),
                "visual_score_top1": round(best.visual_score, 4),
                "margin_top1_top2": round(best.score - candidates[1].score, 4) if len(candidates) > 1 else None,
                "decision_reason": decision.reason,
                "ocr_text": ocr_text,
            },
            box=[round(v, 1) for v in box] if box is not None else None,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
        )

    def top1_slug(self, image: Image.Image) -> str:
        """Для скрипта оценки: всегда лучший кандидат, даже при низкой уверенности."""
        return self.scan(image).top5[0]["slug"]
