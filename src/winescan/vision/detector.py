"""Слой 1: поиск упаковки вина на фото (zero-shot детектор OWLv2).

Детектор с текстовыми подсказками, а не COCO-класс «bottle»: в каталоге есть bag-in-box,
тетрапаки и банки (около 25 вин).
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor

from winescan.vision.embedder import default_device

DEFAULT_DETECTOR = "google/owlv2-base-patch16-ensemble"
DEFAULT_PROMPTS = ("a bottle of wine", "a wine bottle", "a box of wine", "a can of wine", "a carton of wine")


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    score: float
    label: str


class PackageDetector:
    def __init__(
        self,
        model_id: str = DEFAULT_DETECTOR,
        device: str | None = None,
        prompts: tuple[str, ...] = DEFAULT_PROMPTS,
        threshold: float = 0.1,
        max_side: int = 960,
    ):
        self.device = device or default_device()
        self.prompts = prompts
        self.threshold = threshold
        self.max_side = max_side
        self.processor = Owlv2Processor.from_pretrained(model_id)
        self.model = Owlv2ForObjectDetection.from_pretrained(model_id).to(self.device).eval()

    @torch.inference_mode()
    def detect(self, image: Image.Image) -> list[Detection]:
        image = image.convert("RGB")
        small = image.copy()
        small.thumbnail((self.max_side, self.max_side))
        scale = image.width / small.width
        inputs = self.processor(text=[list(self.prompts)], images=small, return_tensors="pt").to(self.device)
        outputs = self.model(**inputs)
        # OWLv2 дополняет кадр до квадрата справа и снизу: рамки нормированы к стороне квадрата
        side = max(small.size)
        result = self.processor.post_process_grounded_object_detection(
            outputs=outputs, threshold=self.threshold, target_sizes=torch.tensor([[side, side]])
        )[0]
        detections = []
        for box, score, label in zip(result["boxes"], result["scores"], result["labels"]):
            x0, y0, x1, y1 = (float(v) * scale for v in box)
            clipped = (max(0.0, x0), max(0.0, y0), min(float(image.width), x1), min(float(image.height), y1))
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                detections.append(Detection(clipped, float(score), self.prompts[int(label)]))
        return detections


def _inside_share(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> float:
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0) / area if area > 0 else 0.0


def choose_main_package(
    detections: list[Detection],
    image_size: tuple[int, int],
    min_score: float = 0.15,
    area_cap: float = 0.1,
    centrality_floor: float = 0.1,
    group_penalty: float = 0.5,
) -> Detection | None:
    """Главная упаковка кадра — первая по весу из :func:`rank_packages`."""
    ranked = rank_packages(detections, image_size, min_score, area_cap, centrality_floor, group_penalty)
    return ranked[0][0] if ranked else None


def box_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def select_candidates(
    ranked: list[tuple[Detection, float]], k: int, max_iou: float = 0.6
) -> list[tuple[Detection, float]]:
    """До k рамок-кандидатов по весу, без почти совпадающих (IoU > max_iou) — для выбора по поиску."""
    chosen: list[tuple[Detection, float]] = []
    for detection, weight in ranked:
        if all(box_iou(detection.box, other.box) <= max_iou for other, _ in chosen):
            chosen.append((detection, weight))
        if len(chosen) == k:
            break
    return chosen


def rank_packages(
    detections: list[Detection],
    image_size: tuple[int, int],
    min_score: float = 0.15,
    area_cap: float = 0.1,
    centrality_floor: float = 0.1,
    group_penalty: float = 0.5,
) -> list[tuple[Detection, float]]:
    """Упаковки кадра по убыванию априорного веса: уверенная, достаточно крупная, ближе к центру.

    Площадь учитывается с потолком ``area_cap``, а рамка, внутри которой лежат ещё две и
    больше уверенных рамок, штрафуется: это группа бутылок, а не одна (фото Массандры
    из публичного набора).

    Версии параметров (доля кадров синтетики с IoU ≥ 0,5, 400 кадров seed 0):
    v1 — min_score=0, area_cap=1, centrality_floor=0,5, group_penalty=1: 0,838;
    v2 — 0,15 / 0,3 / 0,25 / 0,3: 0,882;
    v3 (по умолчанию) — 0,15 / 0,1 / 0,1 / 0,5: 0,905 (подбор по сетке; без штрафа 0,908,
    но штраф оставлен ради реального фото группы бутылок).
    """
    if not detections:
        return []
    width, height = image_size
    confident = [d for d in detections if d.score >= min_score] or detections

    def weight(detection: Detection) -> float:
        x0, y0, x1, y1 = detection.box
        area_share = min(area_cap, (x1 - x0) * (y1 - y0) / (width * height))
        centrality = 1.0 - min(1.0, abs((x0 + x1) / 2 / width - 0.5) * 2)
        contained = sum(
            other is not detection
            and other.score >= 0.5 * detection.score
            and _inside_share(other.box, detection.box) >= 0.8
            for other in confident
        )
        penalty = group_penalty if contained >= 2 else 1.0
        return detection.score * area_share**0.5 * (centrality_floor + (1 - centrality_floor) * centrality) * penalty

    return sorted(((d, weight(d)) for d in confident), key=lambda pair: pair[1], reverse=True)
