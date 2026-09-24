import numpy as np
import pytest

from winescan.search.metric import Metric


def _metric(tmp_path, weight: np.ndarray, **meta) -> Metric:
    path = tmp_path / "metric.npz"
    np.savez(path, weight=weight, **meta)
    return Metric.load(path)


def test_projection_normalises_and_keeps_order(tmp_path):
    # ортогональная матрица: косинусы сохраняются, значит порядок кандидатов не меняется
    weight = np.linalg.qr(np.random.default_rng(0).normal(size=(4, 4)))[0].astype(np.float32)
    metric = _metric(tmp_path, weight, model_id="google/siglip2-so400m-patch14-384")
    vectors = np.array([[1.0, 0.0, 0.0, 0.0], [0.6, 0.8, 0.0, 0.0]], dtype=np.float32)

    projected = metric.project(vectors)

    assert np.allclose(np.linalg.norm(projected, axis=1), 1.0, atol=1e-5)
    assert projected[0] @ projected[1] == pytest.approx(vectors[0] @ vectors[1], abs=1e-5)


def test_projection_accepts_lower_rank(tmp_path):
    metric = _metric(tmp_path, np.eye(2, 4, dtype=np.float32))

    assert metric.project(np.ones((3, 4), dtype=np.float32)).shape == (3, 2)


def test_check_rejects_other_model(tmp_path):
    metric = _metric(tmp_path, np.eye(2, dtype=np.float32), model_id="google/siglip2-so400m-patch14-384",
                     views=np.array(["siglip2-so400m-patch14-384", "siglip2-so400m-patch14-384__label"]))  # fmt: skip

    metric.check("google/siglip2-so400m-patch14-384", ["a", "b"])
    with pytest.raises(ValueError, match="обучено для модели"):
        metric.check("google/siglip2-base-patch16-224", ["a", "b"])
    with pytest.raises(ValueError, match="обучено для видов"):
        metric.check("google/siglip2-so400m-patch14-384", ["a"])


def test_committed_metric_matches_service_model():
    from winescan.config import PROJECT_ROOT

    metric = Metric.load(PROJECT_ROOT / "configs" / "metric_v1.npz")

    assert metric.weight.shape == (512, 1152)
    assert metric.model_id == "google/siglip2-so400m-patch14-384"
    assert np.allclose(np.linalg.norm(metric.project(np.eye(1, 1152, dtype=np.float32)), axis=1), 1.0, atol=1e-5)
