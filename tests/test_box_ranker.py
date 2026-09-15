import pytest

from winescan.search.box_ranker import FEATURES, BoxRanker, box_features, train_box_ranker


def test_box_features_shape_and_values():
    rows = box_features(
        boxes=[(400, 100, 600, 900), (0, 100, 200, 900)],
        det_scores=[0.4, 0.5],
        priors=[1.0, 0.5],
        image_size=(1000, 1000),
        top1s=[0.80, 0.85],
        margins=[0.05, 0.01],
    )

    assert len(rows) == 2 and len(rows[0]) == len(FEATURES)
    first = dict(zip(FEATURES, rows[0]))
    assert first["rank"] == 0 and first["log_prior"] == 0.0 and first["centrality"] == pytest.approx(1.0)
    assert first["top1_gap"] == pytest.approx(-0.05)


def test_trained_ranker_learns_centrality_and_roundtrips(tmp_path):
    rows, labels = [], []
    for i in range(40):
        central = box_features([(450, 100, 550, 900), (0, 100, 100, 900)], [0.3, 0.6], [0.6, 1.0], (1000, 1000),
                               [0.8, 0.9], [0.02, 0.05])  # fmt: skip
        rows += central
        labels += [True, False]
    ranker = BoxRanker(train_box_ranker(rows, labels), meta={"note": "test"})

    assert ranker.choose(rows[:2]) == 0
    ranker.save(tmp_path / "ranker.joblib")
    assert BoxRanker.load(tmp_path / "ranker.joblib").choose(rows[:2]) == 0
