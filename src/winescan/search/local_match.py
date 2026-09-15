"""Слой 3: переранжирование по локальным признакам (SIFT + RANSAC).

Глобальный эмбеддинг SigLIP почти не различает вина одной серии: этикетки отличаются
только надписями. Локальные признаки видят именно такие отличия: у правильного эталона
больше ключевых точек, согласованных одним проективным преобразованием (этикетка —
почти плоская). Это классическое CV без обучения; считается только для top-K кандидатов.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from winescan.search.rerank import local_bonus

MATCH_SIDE = 800  # сторона, к которой приводятся кропы перед SIFT
MIN_MATCHES_FOR_RANSAC = 8


@dataclass(frozen=True)
class Features:
    keypoints: np.ndarray  # (n, 2) координаты
    descriptors: np.ndarray | None  # (n, 128) float32


_sift = None
_clahe = None


def _tools():
    global _sift, _clahe
    if _sift is None:
        _sift = cv2.SIFT_create(nfeatures=2000)
        _clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return _sift, _clahe


def extract(image: Image.Image, side: int = MATCH_SIDE) -> Features:
    """SIFT на серой копии с выравниванием контраста (CLAHE): блики и тени мешают меньше."""
    sift, clahe = _tools()
    gray = image.convert("L")
    gray.thumbnail((side, side))
    array = clahe.apply(np.asarray(gray))
    keypoints, descriptors = sift.detectAndCompute(array, None)
    points = np.array([kp.pt for kp in keypoints], dtype=np.float32).reshape(-1, 2)
    return Features(points, descriptors)


def inliers(query: Features, reference: Features, ratio: float = 0.75) -> int:
    """Число совпадений, согласованных гомографией (0 — совпадений нет)."""
    if query.descriptors is None or reference.descriptors is None:
        return 0
    if len(query.descriptors) < 2 or len(reference.descriptors) < 2:
        return 0
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(query.descriptors, reference.descriptors, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < ratio * n.distance]
    if len(good) < MIN_MATCHES_FOR_RANSAC:
        return 0
    src = query.keypoints[[m.queryIdx for m in good]]
    dst = reference.keypoints[[m.trainIdx for m in good]]
    _, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 8.0)
    return int(mask.sum()) if mask is not None else 0


__all__ = ["Features", "extract", "inliers", "local_bonus"]
