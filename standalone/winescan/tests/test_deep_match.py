"""Интерфейс ALIKED + LightGlue без моделей: подменяем их и проверяем разбор совпадений.

Сами модели требуют GPU и весов из сети, поэтому в тестах не участвуют. Здесь важно, что
``match`` возвращает такой же ``MatchResult``, как SIFT-сопоставитель, — на этом держится
возможность подставить его в ``verify``.
"""

import numpy as np

from winescan.search.deep_match import DeepFeatures, DeepMatcher


class _FakeTensor:
    """Минимальная замена тензора: нужен только .cpu().numpy()."""

    def __init__(self, array: np.ndarray):
        self._array = array

    def cpu(self) -> "_FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self._array


def _features(points: np.ndarray) -> DeepFeatures:
    return DeepFeatures(points.astype(np.float32), {"keypoints": points})


def _grid(step: float = 40.0, side: int = 6) -> np.ndarray:
    return np.array([[x * step, y * step] for x in range(side) for y in range(side)], dtype=np.float32)


def _matcher_returning(pairs: np.ndarray, monkeypatch) -> DeepMatcher:
    monkeypatch.setattr(DeepMatcher, "_models", lambda self: (None, lambda _: {"matches": [_FakeTensor(pairs)]}))
    return DeepMatcher(device="cpu")


def test_match_builds_homography_from_pairs(monkeypatch):
    query_points = _grid()
    reference_points = query_points + np.array([12.0, -5.0], dtype=np.float32)
    pairs = np.stack([np.arange(len(query_points)), np.arange(len(query_points))], axis=1)

    result = _matcher_returning(pairs, monkeypatch).match(_features(query_points), _features(reference_points))

    assert result.inliers == len(query_points) and result.good_matches == len(query_points)
    assert result.homography is not None
    moved = result.homography @ np.array([query_points[0][0], query_points[0][1], 1.0])
    assert np.allclose(moved[:2] / moved[2], reference_points[0], atol=1e-3)


def test_match_without_pairs_is_empty(monkeypatch):
    points = _grid()

    result = _matcher_returning(np.zeros((0, 2), dtype=np.int64), monkeypatch).match(_features(points), _features(points))

    assert result.inliers == 0 and result.good_matches == 0 and result.homography is None
    assert len(result.reference_points) == 0


def test_inliers_shortcut_matches_match(monkeypatch):
    points = _grid()
    pairs = np.stack([np.arange(len(points)), np.arange(len(points))], axis=1)
    matcher = _matcher_returning(pairs, monkeypatch)

    assert matcher.inliers(_features(points), _features(points)) == matcher.match(_features(points), _features(points)).inliers
