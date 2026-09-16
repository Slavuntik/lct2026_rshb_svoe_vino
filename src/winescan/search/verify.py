"""Слой 3: признаки проверки кандидата (вместо голого log(1 + inliers)).

Абсолютное число inliers смещено к эталонам с плотной текстурой: на реальном фото Массандры
у верного «Мускателя белого» 63 inliers, у «Мускателя розового» той же вёрстки — 87. Поэтому
считаем признаки, нормированные на эталон, и сравнение пикселей после выравнивания:

- ``inlier_ratio`` — доля согласованных совпадений среди прошедших ratio test;
- ``coverage`` — доля клеток сетки 6×6 по упаковке эталона, где есть согласованные точки;
- ``ncc`` — нормированная корреляция яркости запроса, перенесённого гомографией на эталон;
- ``color_distance`` — разница цвета (a*, b* в Lab) после нормализации «серый мир» на обеих
  картинках: различает белую и розовую этикетку, которых SIFT по яркости не видит.

Сравнение пикселей учитывает только общую видимую область и отбрасывает пересвеченные
пиксели бликов. Веса признаков подбираются обученным слиянием (PLAN.md, 2.5).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np
from PIL import Image

from winescan.search.local_match import Features, MatchResult, match

GRID = 6
MIN_OVERLAP_PIXELS = 500


@dataclass(frozen=True)
class Verification:
    inliers: int
    inlier_ratio: float
    coverage: float
    ncc: float
    color_distance: float
    overlap: float  # доля упаковки эталона, покрытая выровненным запросом

    def as_dict(self) -> dict:
        return asdict(self)


UNVERIFIED = Verification(0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _rgb_on_white(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    rgba = np.asarray(image.convert("RGBA")).astype(np.float32)
    alpha = rgba[..., 3:] / 255.0
    rgb = rgba[..., :3] * alpha + 255.0 * (1.0 - alpha)
    return rgb.astype(np.uint8), alpha[..., 0] > 0.5


def _coverage(points: np.ndarray, mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if not len(xs) or not len(points):
        return 0.0
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    cells = set()
    for x, y in points:
        if x0 <= x < x1 and y0 <= y < y1:
            cells.add((int((x - x0) * GRID / (x1 - x0)), int((y - y0) * GRID / (y1 - y0))))
    return len(cells) / (GRID * GRID)


def _gray_world(lab_ab: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return lab_ab - lab_ab[mask].mean(axis=0)


def compare_aligned(query: Image.Image, reference: Image.Image, homography: np.ndarray) -> tuple[float, float, float]:
    """(ncc, color_distance, overlap) запроса, перенесённого на эталон гомографией.

    ``query`` и ``reference`` — в масштабе, в котором считалась гомография (local_match.prepare).
    """
    reference_rgb, reference_mask = _rgb_on_white(reference)
    height, width = reference_mask.shape
    query_rgb = np.asarray(query.convert("RGB"))
    warped = cv2.warpPerspective(query_rgb, homography, (width, height), flags=cv2.INTER_LINEAR)
    valid = cv2.warpPerspective(np.full(query_rgb.shape[:2], 255, np.uint8), homography, (width, height)) > 0
    # блики: почти белые и ненасыщенные пиксели не сравниваем
    hsv = cv2.cvtColor(warped, cv2.COLOR_RGB2HSV)
    not_glare = ~((hsv[..., 2] > 240) & (hsv[..., 1] < 30))
    common = reference_mask & valid & not_glare
    overlap = float((reference_mask & valid).sum() / max(reference_mask.sum(), 1))
    if common.sum() < MIN_OVERLAP_PIXELS:
        return 0.0, 1.0, overlap

    gray_q = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY).astype(np.float32)[common]
    gray_r = cv2.cvtColor(reference_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)[common]
    gray_q, gray_r = gray_q - gray_q.mean(), gray_r - gray_r.mean()
    ncc = float((gray_q * gray_r).sum() / (np.sqrt((gray_q**2).sum() * (gray_r**2).sum()) + 1e-6))

    lab_q = cv2.cvtColor(warped, cv2.COLOR_RGB2LAB).astype(np.float32)[..., 1:]
    lab_r = cv2.cvtColor(reference_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)[..., 1:]
    difference = _gray_world(lab_q, common)[common] - _gray_world(lab_r, common)[common]
    color_distance = float(np.linalg.norm(difference, axis=1).mean() / 128.0)
    return ncc, color_distance, overlap


def verify(
    query_image: Image.Image,
    query_features: Features,
    reference_image: Image.Image,
    reference_features: Features,
    matcher=match,
) -> Verification:
    """Признаки проверки пары «кроп запроса ↔ вырезка эталона» (оба в масштабе local_match.prepare).

    ``matcher`` — функция сопоставления: по умолчанию SIFT, может быть ``DeepMatcher.match``
    (ALIKED + LightGlue). Признаки при этом считаются одинаково, поэтому сравнимы."""
    result: MatchResult = matcher(query_features, reference_features)
    if result.homography is None or result.inliers == 0:
        return Verification(result.inliers, 0.0, 0.0, 0.0, 1.0, 0.0)
    _, reference_mask = _rgb_on_white(reference_image)
    ncc, color_distance, overlap = compare_aligned(query_image, reference_image, result.homography)
    return Verification(
        inliers=result.inliers,
        inlier_ratio=result.inliers / max(result.good_matches, 1),
        coverage=_coverage(result.reference_points, reference_mask),
        ncc=ncc,
        color_distance=color_distance,
        overlap=overlap,
    )
