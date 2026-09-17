import numpy as np
import pytest

from winescan.search.fusion import FusionModel, candidate_features, top1_accuracy, train


def _queries(n: int = 60, seed: int = 0) -> dict[str, list[dict]]:
    """Серии-близнецы: визуально неверный кандидат чуть выше, но верный лучше по цвету и покрытию."""
    rng = np.random.default_rng(seed)
    queries = {}
    for i in range(n):
        right_visual, wrong_visual = 0.80 + rng.normal(0, 0.01), 0.81 + rng.normal(0, 0.01)
        best = max(right_visual, wrong_visual)
        right = candidate_features(right_visual, best, {"inliers": 60, "inlier_ratio": 0.5, "coverage": 0.6,
                                                        "ncc": 0.7, "color_distance": 0.05, "overlap": 0.9})  # fmt: skip
        wrong = candidate_features(wrong_visual, best, {"inliers": 80, "inlier_ratio": 0.3, "coverage": 0.4,
                                                        "ncc": 0.5, "color_distance": 0.30, "overlap": 0.9})  # fmt: skip
        queries[f"q{i}"] = [{"slug": "right", "features": right, "label": True},
                            {"slug": "wrong", "features": wrong, "label": False}]  # fmt: skip
    return queries


def test_trained_model_beats_visual_only_on_series_twins(tmp_path):
    queries = _queries()
    rows = [c for candidates in queries.values() for c in candidates]

    model = train(rows)
    visual_only = FusionModel({"visual": 1.0}, 0.0, {}, {})

    assert top1_accuracy(model, _queries(seed=1)) == 1.0
    assert top1_accuracy(visual_only, _queries(seed=1)) < 0.5
    assert model.weights["color_distance"] < 0

    model.save(tmp_path / "fusion.json")
    restored = FusionModel.load(tmp_path / "fusion.json")
    features = queries["q0"][0]["features"]
    assert restored.logit(features) == pytest.approx(model.logit(features))


def test_candidate_features_defaults_without_verification():
    features = candidate_features(0.7, 0.75, None)

    assert features["gap"] == pytest.approx(-0.05)
    assert features["log_inliers"] == 0.0 and features["color_distance"] == 1.0 and features["field_score"] == 0.0
