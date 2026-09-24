"""Слой 3: локальные признаки ALIKED + LightGlue как альтернатива SIFT (PLAN.md, 2.2).

SIFT считает точки по градиентам яркости и на реальных фото даёт от 5 до 87 согласованных
совпадений — этого мало, чтобы отличить вина одной серии. ALIKED — обучаемый детектор точек,
LightGlue — обучаемый сопоставитель; вместе они устойчивее к бликам, наклону и смазу.

Лицензии: LightGlue — Apache-2.0, ALIKED — BSD-3 (RESEARCH.md, раздел 6). Веса скачиваются в
кэш torch.hub при первом запуске. Модели работают на GPU и грузятся лениво, поэтому импорт
модуля ничего не тянет: сервис с ``WINESCAN_LOCAL_FEATURES=sift`` их не создаёт.

Интерфейс совпадает с ``local_match``: ``extract`` возвращает признаки одного кропа,
``match`` — ``MatchResult`` с гомографией и точками эталона, так что ``verify`` работает с
обоими сопоставителями.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from winescan.search.local_match import MATCH_SIDE, homography_result, MatchResult

MAX_KEYPOINTS = 1024


@dataclass(frozen=True)
class DeepFeatures:
    keypoints: np.ndarray  # (n, 2) координаты в масштабе extract
    tensors: dict[str, Any]  # то, что ждёт LightGlue: keypoints, descriptors, image_size


class DeepMatcher:
    """ALIKED + LightGlue на одном устройстве. Модели создаются при первом обращении."""

    def __init__(self, device: str | None = None, max_keypoints: int = MAX_KEYPOINTS):
        self.max_keypoints = max_keypoints
        self._device = device
        self._extractor = None
        self._matcher = None

    def _models(self):
        if self._extractor is None:
            import torch
            from lightglue import ALIKED, LightGlue

            self._device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._extractor = ALIKED(max_num_keypoints=self.max_keypoints).eval().to(self._device)
            self._matcher = LightGlue(features="aliked").eval().to(self._device)
        return self._extractor, self._matcher

    @property
    def device(self) -> str:
        self._models()
        return self._device

    def extract(self, image: Image.Image, side: int = MATCH_SIDE) -> DeepFeatures:
        import torch

        extractor, _ = self._models()
        rgb = image.convert("RGB")
        rgb.thumbnail((side, side))
        tensor = torch.from_numpy(np.asarray(rgb).copy()).permute(2, 0, 1).float().to(self._device) / 255.0
        with torch.inference_mode():
            features = extractor.extract(tensor)
        return DeepFeatures(features["keypoints"][0].cpu().numpy(), features)

    def match(self, query: DeepFeatures, reference: DeepFeatures) -> MatchResult:
        import torch

        _, matcher = self._models()
        with torch.inference_mode():
            pairs = matcher({"image0": query.tensors, "image1": reference.tensors})["matches"][0].cpu().numpy()
        if not len(pairs):
            return MatchResult(0, 0, None, np.zeros((0, 2), np.float32))
        return homography_result(query.keypoints[pairs[:, 0]], reference.keypoints[pairs[:, 1]], len(pairs))

    def inliers(self, query: DeepFeatures, reference: DeepFeatures) -> int:
        return self.match(query, reference).inliers
