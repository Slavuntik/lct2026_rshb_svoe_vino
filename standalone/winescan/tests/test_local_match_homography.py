import numpy as np

from winescan.search.local_match import MIN_MATCHES_FOR_RANSAC, homography_result


def _grid(step: float = 40.0, side: int = 6) -> np.ndarray:
    return np.array([[x * step, y * step] for x in range(side) for y in range(side)], dtype=np.float32)


def test_homography_result_finds_shift_and_keeps_reference_points():
    source = _grid()
    target = source + np.array([15.0, -7.0], dtype=np.float32)

    result = homography_result(source, target, good_matches=len(source))

    assert result.inliers == len(source) and result.good_matches == len(source)
    assert result.homography is not None
    moved = result.homography @ np.array([source[0][0], source[0][1], 1.0])
    assert np.allclose(moved[:2] / moved[2], target[0], atol=1e-3)
    assert result.reference_points.shape == (len(source), 2)


def test_homography_result_rejects_too_few_matches():
    source = _grid(side=2)[: MIN_MATCHES_FOR_RANSAC - 1]

    result = homography_result(source, source.copy(), good_matches=len(source))

    assert result.inliers == 0 and result.homography is None
    assert result.good_matches == len(source) and len(result.reference_points) == 0


def test_homography_result_drops_outliers():
    source = _grid()
    target = source.copy()
    target[:5] += np.array([300.0, -250.0], dtype=np.float32)  # заведомо неверные пары

    result = homography_result(source, target, good_matches=len(source))

    assert result.inliers == len(source) - 5
