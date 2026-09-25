import numpy as np
from shelf_api.policy import independent, plausible, select


def test_ambiguous_and_nonfinite_matches_are_rejected():
    best = {"id": "a", "inliers": 30, "matches": 40, "coverage": 0.2}
    assert select([best]) == "a"
    assert select([best, {**best, "id": "b", "inliers": 25}]) is None
    assert select([{**best, "coverage": float("nan")}]) is None
    assert select([{**best, "inliers": 19}]) is None
    assert select([{**best, "inliers": 19, "matches": 30}], True) == "a"
    assert not independent({**best, "inliers": 13})


def test_projection_rejects_mirror_and_severe_tilt():
    assert plausible(np.eye(3), (128, 512), (128, 512))
    assert not plausible(
        np.array([[-1, 0, 128], [0, 1, 0], [0, 0, 1]]), (128, 512), (128, 512)
    )
    assert not plausible(
        np.array([[1, 0, 0], [2, 1, 0], [0, 0, 1]]), (128, 512), (128, 512)
    )


def test_identical_reference_galleries_return_all_alternatives():
    from shelf_api.policy import reference_ambiguities

    index = {
        "ids": ["a", "b", "c"],
        "references": {"a": "a", "b": "b", "c": "c"},
        "verificationReferences": {"a": "aa", "b": "bb", "c": "cc"},
    }
    hashes = {"a": "x", "b": "x", "c": "x", "aa": "y", "bb": "y", "cc": "z"}
    assert reference_ambiguities(index, hashes) == {"a": ["b"], "b": ["a"]}
