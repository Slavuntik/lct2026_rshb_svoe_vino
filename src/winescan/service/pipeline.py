"""Пайплайн распознавания одного фото.

детекция упаковки -> до K рамок -> эмбеддинги (несколько индексов) -> выбор рамки по поиску
-> top-K вин -> проверка кандидатов (SIFT, покрытие, NCC, цвет) -> слияние (обученная модель
или прежние ручные веса) -> [поля этикетки VLM, если кандидаты близки] -> решение -> карточка.
Все параметры — ScannerConfig, переопределяются переменными окружения (README.md).
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from winescan.config import PROJECT_ROOT, get_paths
from winescan.search.box_selection import PRIOR_ONLY, choose_box
from winescan.search.rerank import decide, fuse
from winescan.vision.preprocess import crop_box

MAX_QUERY_SIDE = 1600
SEARCH_TOP_K = 10


def _env_list(name: str, default: tuple, cast=str) -> tuple:
    value = os.environ.get(name)
    return tuple(cast(v.strip()) for v in value.split(",") if v.strip()) if value else default


def _env_float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


def _env_path(name: str, default: str | None) -> str | None:
    """Путь к модели: пусто — значение по умолчанию, ``0`` или ``none`` — модель выключена."""
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return None if value.lower() in ("0", "none") else value


@dataclass(frozen=True)
class ScannerConfig:
    # мультиракурсная галерея (повороты эталона −30…+30°): synth_v2 top-1 0,618 → 0,642 на первой рамке
    indexes: tuple[str, ...] = (
        "siglip2-so400m-patch14-384__yaw-30_-15_0_15_30",
        "siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30",
    )
    index_weights: tuple[float, ...] = (0.5, 0.5)
    device: str | None = None
    use_detector: bool = True
    # выбор из K рамок детектора; 1 — первая рамка по априорному весу
    box_candidates: int = 3
    box_rule: dict = field(default_factory=lambda: dict(PRIOR_ONLY))
    # обучаемый выбор рамки (search.box_ranker), заменяет box_rule при box_candidates > 1;
    # обучен на скорах той же галереи: synth_v2 top-1 0,642 → 0,661
    box_ranker_path: str | None = "configs/box_ranker_v2.joblib"
    local_top: int = 5
    # прежнее ручное слияние: на синтетике top-1 выходит на плато с 0,2, но на реальном фото серии
    # Массандры вес > 0,24 переставляет неверное вино вперёд (ARCHITECTURE.md, D10)
    local_weight: float = 0.15
    use_ocr: bool = True
    text_weight: float = 0.02
    # обученное слияние (search.fusion): признаки проверки и логит для отказа
    fusion_path: str | None = "configs/fusion_v2.json"
    # True — порядок кандидатов по логиту слияния; False — гибрид: порядок по ручному слиянию
    # (local_weight, text_weight) на inliers проверки, логит — в деталях ответа и для отказа.
    # Гибрид на общих отложенных запросах synth_v2: top-1 0,702 против 0,690 у прежних умолчаний
    # (6:0 по несовпадающим ответам) и 0,687 у ранжирования слиянием
    fusion_rank: bool = False
    # отказ по порогу логита слияния (meta.reject_logit): AUROC 0,815 против 0,777 у визуального скора (v1+v2);
    # при 2% ложных отказов отклоняет лишь ~3% вин вне каталога (WORKLOG, «Утечка в негативах»), а порог
    # попадает в скопление логитов кандидатов без совпадений SIFT — поэтому по умолчанию выключен
    fusion_reject: bool = False
    # поля этикетки VLM только когда отрыв лучшего кандидата (в логитах слияния) меньше порога
    use_vlm: bool = False
    vlm_margin: float = 1.0
    # на синтетике отсекает 2,6% запросов из каталога; реальные вина вне каталога — 0,66 и 0,70
    min_visual_score: float | None = 0.74
    min_margin: float | None = None

    @classmethod
    def from_env(cls) -> ScannerConfig:
        base = cls()
        rule = os.environ.get("WINESCAN_BOX_RULE")
        return cls(
            indexes=_env_list("WINESCAN_INDEXES", base.indexes),
            index_weights=_env_list("WINESCAN_INDEX_WEIGHTS", base.index_weights, float),
            device=os.environ.get("WINESCAN_DEVICE") or None,
            use_detector=os.environ.get("WINESCAN_USE_DETECTOR", "1") != "0",
            box_candidates=int(os.environ.get("WINESCAN_BOX_CANDIDATES", base.box_candidates)),
            box_rule=json.loads(rule) if rule else base.box_rule,
            box_ranker_path=_env_path("WINESCAN_BOX_RANKER", base.box_ranker_path),
            local_top=int(os.environ.get("WINESCAN_LOCAL_TOP", base.local_top)),
            local_weight=_env_float("WINESCAN_LOCAL_WEIGHT", base.local_weight),
            use_ocr=os.environ.get("WINESCAN_USE_OCR", "1") != "0",
            text_weight=_env_float("WINESCAN_TEXT_WEIGHT", base.text_weight),
            fusion_path=_env_path("WINESCAN_FUSION", base.fusion_path),
            fusion_rank=os.environ.get("WINESCAN_FUSION_RANK", "1" if base.fusion_rank else "0") != "0",
            fusion_reject=os.environ.get("WINESCAN_FUSION_REJECT", "1" if base.fusion_reject else "0") != "0",
            use_vlm=os.environ.get("WINESCAN_USE_VLM", "0") == "1",
            vlm_margin=_env_float("WINESCAN_VLM_MARGIN", base.vlm_margin),
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
        from winescan.search.local_features import STORE_NAME, LocalFeatureStore
        from winescan.search.multi import MultiIndexSearcher

        paths = get_paths()
        self.config = config
        self.uploads_dir = paths.uploads_dir
        self.cards = load_cards(paths.artifacts_dir / "catalog" / "catalog.jsonl")
        self.searcher = MultiIndexSearcher(list(config.indexes), list(config.index_weights), config.device)
        self.detector = self.reader = self.vlm = self.fusion = None
        if config.use_detector:
            from winescan.vision.detector import PackageDetector

            self.detector = PackageDetector(device=config.device)
        if config.fusion_path:
            from winescan.search.fusion import FusionModel

            path = Path(config.fusion_path)
            self.fusion = FusionModel.load(path if path.is_absolute() else PROJECT_ROOT / path)
        if config.use_ocr and config.text_weight > 0 and not (config.fusion_path and config.fusion_rank):
            from winescan.vision.ocr import LabelReader

            self.reader = LabelReader(gpu=self.searcher.device.startswith("cuda"))
        if config.use_vlm:
            from winescan.vision.vlm import LabelFieldReader

            self.vlm = LabelFieldReader(device=config.device)
        self.box_ranker = None
        if config.box_ranker_path:
            from winescan.search.box_ranker import BoxRanker

            path = Path(config.box_ranker_path)
            self.box_ranker = BoxRanker.load(path if path.is_absolute() else PROJECT_ROOT / path)
        self._det_scores: list[float] = [1.0]
        store_dir = paths.artifacts_dir / "index" / STORE_NAME
        # без хранилища признаки эталонов считаются на лету: первый запрос с новым кандидатом дольше
        self.local_store = LocalFeatureStore(store_dir) if store_dir.exists() else None
        self._reference = lru_cache(maxsize=4096)(self._load_reference)
        self._lock = threading.Lock()  # модели на одном GPU: запросы обрабатываются по очереди

    def _load_reference(self, slug: str):
        """(вырезка эталона в масштабе признаков, SIFT-признаки)."""
        from winescan.search.local_features import load_reference_view, reference_features, reference_view

        path = self.uploads_dir / self.cards[slug]["image"]["file"]
        view = load_reference_view(slug) or reference_view(path)
        features = self.local_store.get(slug) if self.local_store is not None and slug in self.local_store else reference_features(path)
        return view, features

    def warmup(self) -> None:
        """Прогрев на кадре с текстурой и текстом: инициализирует детектор, OCR, SIFT и CUDA-ядра
        для рабочих размеров. На пустом кадре OCR и SIFT не запускались, и первый реальный запрос
        скрипта оценки занимал 8 с."""
        rng = np.random.default_rng(0)
        image = Image.fromarray(rng.integers(0, 255, (1600, 1200, 3), dtype=np.uint8))
        draw = ImageDraw.Draw(image)
        draw.rectangle([400, 500, 800, 1300], fill=(90, 20, 30))
        draw.rectangle([430, 850, 770, 1150], fill="white")
        draw.text((460, 950), "WINE 2024", fill="black")
        for _ in range(2):
            self.scan(image)

    def _boxes(self, image: Image.Image, timings: dict) -> list[tuple[tuple | None, float]]:
        from winescan.vision.detector import rank_packages, select_candidates

        self._det_scores = [1.0]
        if self.detector is None:
            return [(None, 1.0)]
        step = time.perf_counter()
        ranked = rank_packages(self.detector.detect(image), image.size)
        timings["detect"] = (time.perf_counter() - step) * 1000
        chosen = select_candidates(ranked, max(1, self.config.box_candidates))
        if not chosen:
            return [(None, 1.0)]
        self._det_scores = [detection.score for detection, _ in chosen]
        return [(detection.box, weight) for detection, weight in chosen]

    def scan(self, image: Image.Image) -> ScanResult:
        config = self.config
        timings: dict[str, float] = {}
        began = time.perf_counter()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_QUERY_SIDE, MAX_QUERY_SIDE))

        with self._lock:
            boxes = self._boxes(image, timings)
            step = time.perf_counter()
            vectors = self.searcher.embed_views([image] * len(boxes), [box for box, _ in boxes])
            scores = self.searcher.wine_scores(vectors)
            if len(boxes) > 1:
                sorted_scores = -np.sort(-scores, axis=1)[:, :2]
                top1s, margins = sorted_scores[:, 0].tolist(), (sorted_scores[:, 0] - sorted_scores[:, 1]).tolist()
                if self.box_ranker is not None:
                    from winescan.search.box_ranker import box_features

                    rows = box_features([box for box, _ in boxes], [d for d in self._det_scores],
                                        [w for _, w in boxes], image.size, top1s, margins)  # fmt: skip
                    chosen = self.box_ranker.choose(rows)
                else:
                    chosen = choose_box([w for _, w in boxes], top1s, margins, config.box_rule)
            else:
                chosen = 0
            box = boxes[chosen][0]
            order = np.argsort(-scores[chosen])[:SEARCH_TOP_K]
            slugs = [self.searcher.wine_slugs[i] for i in order]
            visual = [float(scores[chosen, i]) for i in order]
            timings["embed_search"] = (time.perf_counter() - step) * 1000
            package = crop_box(image, box) if box else image

            if self.fusion is not None:
                ranked, details, ocr_text = self._fused(package, slugs, visual, timings)
            else:
                ranked, details, ocr_text = self._legacy(package, slugs, visual, timings)

        # с обученным слиянием отказ — по порогу его логита (подобран на leave-one-out негативах);
        # визуальный порог при этом тоже действует, если задан
        reject = self.fusion.meta.get("reject_logit") if self.fusion is not None and config.fusion_reject else None
        # в гибриде порядок ручной, а уверенность — логит слияния для лучшего кандидата
        confidence = details.get(ranked[0].slug, {}).get("logit") if self.fusion is not None and not config.fusion_rank else None
        decision = decide(ranked, config.min_visual_score, config.min_margin, reject, confidence)
        timings["total"] = (time.perf_counter() - began) * 1000
        best = ranked[0]
        found = decision.status == "found"
        margin = best.score - ranked[1].score if len(ranked) > 1 else None
        return ScanResult(
            status=decision.status,
            slug=best.slug if found else None,
            card=self.cards.get(best.slug) if found else None,
            top5=[
                {"slug": c.slug, "name": self.cards.get(c.slug, {}).get("name"), "score": round(c.score, 4),
                 "visual_score": round(c.visual_score, 4), "local_inliers": c.local_inliers,
                 "text_score": round(c.text_score, 3), **details.get(c.slug, {})}
                for c in ranked[:5]
            ],  # fmt: skip
            confidence={
                "score_top1": round(best.score, 4),
                "visual_score_top1": round(best.visual_score, 4),
                "margin_top1_top2": round(margin, 4) if margin is not None else None,
                "band": "low" if not found else ("high" if margin is None or margin >= self._band_margin() else "medium"),
                "decision_reason": decision.reason,
                "ocr_text": ocr_text,
                "boxes_considered": len(boxes),
            },
            box=[round(v, 1) for v in box] if box is not None else None,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
        )

    def _band_margin(self) -> float:
        # отрыв в логитах обученного слияния или в скорах ручного слияния (в том числе в гибриде)
        return self.config.vlm_margin if self.fusion is not None and self.config.fusion_rank else 0.02

    def _legacy(self, package: Image.Image, slugs: list[str], visual: list[float], timings: dict):
        from winescan.search.local_match import extract, inliers

        config = self.config
        local = None
        if config.local_weight > 0 and config.local_top > 0:
            step = time.perf_counter()
            query_features = extract(package)
            local = {s: inliers(query_features, self._reference(s)[1]) for s in slugs[: config.local_top]
                     if self.cards.get(s, {}).get("image", {}).get("file")}  # fmt: skip
            timings["local_match"] = (time.perf_counter() - step) * 1000
        texts, ocr_text = self._read_text(package, slugs, timings)
        return fuse(slugs, visual, local, texts, config.local_weight, config.text_weight), {}, ocr_text

    def _read_text(self, package: Image.Image, slugs: list[str], timings: dict) -> tuple[dict | None, str | None]:
        """Сходство текста этикетки (OCR) с карточками кандидатов; без OCR — (None, None)."""
        from winescan.search.text_match import LabelText, text_score

        if self.reader is None:
            return None, None
        step = time.perf_counter()
        ocr_text = self.reader.read(package)
        label = LabelText.from_ocr(ocr_text)
        texts = {s: text_score(self.cards[s], label) for s in slugs if s in self.cards}
        timings["ocr"] = (time.perf_counter() - step) * 1000
        return texts, ocr_text

    def _fused(self, package: Image.Image, slugs: list[str], visual: list[float], timings: dict):
        from winescan.search.fields import field_score
        from winescan.search.fusion import candidate_features
        from winescan.search.local_match import extract, prepare
        from winescan.search.rerank import Candidate
        from winescan.search.verify import verify

        config = self.config
        step = time.perf_counter()
        query_image = prepare(package)
        query_features = extract(query_image)
        top = slugs[: config.local_top]
        verifications = {}
        for slug in top:
            if self.cards.get(slug, {}).get("image", {}).get("file"):
                reference_image, reference_features = self._reference(slug)
                verifications[slug] = verify(query_image, query_features, reference_image, reference_features).as_dict()
        timings["verify"] = (time.perf_counter() - step) * 1000

        def ranking(fields=None):
            candidates = []
            for slug, score in zip(top, visual):
                score_fields = field_score(self.cards[slug], fields) if fields is not None else 0.0
                features = candidate_features(score, visual[0], verifications.get(slug), score_fields)
                candidates.append({"slug": slug, "features": features, "visual": score, "field_score": score_fields})
            return self.fusion.rank(candidates)

        ranked = ranking()
        ocr_text = None
        if self.vlm is not None and len(ranked) > 1 and ranked[0][0] - ranked[1][0] < config.vlm_margin:
            step = time.perf_counter()
            fields = self.vlm.read(package)
            ranked = ranking(fields)
            ocr_text = fields.all_text().strip() or None
            timings["vlm"] = (time.perf_counter() - step) * 1000

        details = {
            c["slug"]: {"logit": round(logit, 3), "probability": round(1 / (1 + math.exp(-logit)), 3),
                        **{k: round(v, 3) for k, v in (verifications.get(c["slug"]) or {}).items()},
                        "field_score": round(c["field_score"], 3)}
            for logit, c in ranked
        }  # fmt: skip
        if not config.fusion_rank:
            # гибрид: порядок — ручное слияние (как _legacy) на inliers той же проверки
            local = {slug: int(v.get("inliers", 0)) for slug, v in verifications.items()}
            texts, text = self._read_text(package, slugs, timings)
            return fuse(slugs, visual, local, texts, config.local_weight, config.text_weight), details, ocr_text or text
        candidates = [
            Candidate(c["slug"], logit, c["visual"], int((verifications.get(c["slug"]) or {}).get("inliers", 0)), c["field_score"])
            for logit, c in ranked
        ]
        # хвост визуального top-K без проверки — после проверенных, по визуальному скору
        tail_score = min(logit for logit, _ in ranked) - 1.0
        candidates += [Candidate(s, tail_score - (visual[0] - v), v) for s, v in zip(slugs[len(top):], visual[len(top):])]
        return candidates, details, ocr_text

    def top1_slug(self, image: Image.Image) -> str:
        """Для скрипта оценки: всегда лучший кандидат, даже при низкой уверенности."""
        return self.scan(image).top5[0]["slug"]
