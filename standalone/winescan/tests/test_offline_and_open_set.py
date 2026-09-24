import numpy as np
import pytest

from winescan.eval.metrics import auroc, open_set_at_threshold
from winescan.eval.offline import QueryCache, choose_slot, evaluate_rule, top2
from winescan.search.index import VectorIndex
from winescan.vision.detector import Detection, rank_packages, select_candidates


def _unit(*values):
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def test_auroc_perfect_random_and_ties():
    assert auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert auroc([0.1], [0.9]) == 0.0
    assert auroc([0.5, 0.5], [0.5]) == pytest.approx(0.5)


def test_open_set_accuracy_counts_rejections():
    result = open_set_at_threshold(np.array([True, False]), np.array([0.9, 0.8]), np.array([0.5, 0.85]), 0.7)

    # позитив 1 верен и отвечен, позитив 2 неверен; негатив 0,5 отклонён, 0,85 — нет
    assert result["open_set_accuracy"] == pytest.approx(2 / 4)
    assert result["out_of_catalog_rejected"] == 0.5


def test_top2_leave_one_out():
    scores = np.array([0.2, 0.9, 0.7, 0.1])

    assert top2(scores) == (1, pytest.approx(0.9), pytest.approx(0.2))
    assert top2(scores, exclude=1)[0] == 2


def test_select_candidates_skips_overlapping_boxes():
    detections = [
        Detection((100, 100, 300, 900), 0.5, "bottle"),
        Detection((110, 110, 310, 910), 0.45, "bottle"),  # почти та же рамка
        Detection((500, 100, 700, 900), 0.4, "bottle"),
    ]
    chosen = select_candidates(rank_packages(detections, (800, 1000)), k=2)

    assert [d.box for d, _ in chosen] == [(100, 100, 300, 900), (500, 100, 700, 900)] or \
        [d.box for d, _ in chosen] == [(500, 100, 700, 900), (100, 100, 300, 900)]  # fmt: skip
    assert len(chosen) == 2


def _cache():
    index = VectorIndex(slugs=["a", "b", "c"], vectors=np.stack([_unit(1, 0, 0), _unit(0, 1, 0), _unit(0, 0, 1)]))
    records = [
        {"expected_slug": "b", "shares_image": False,
         "slots": [{"kind": "det", "prior": 1.0}, {"kind": "det", "prior": 0.9}, {"kind": "full"}]},
    ]  # fmt: skip
    vectors = np.zeros((1, 3, 1, 3), dtype=np.float16)
    vectors[0, 0, 0] = _unit(0.9, 0.44, 0)  # априорно лучшая рамка — сосед «a»
    vectors[0, 1, 0] = _unit(0, 1, 0.05)  # вторая рамка уверенно находит «b»
    vectors[0, 2, 0] = _unit(0.6, 0.6, 0.5)
    return QueryCache.from_arrays(records, vectors, [index], [1.0])


def test_choose_slot_prior_vs_retrieval():
    cache = _cache()

    assert choose_slot(cache, 0, {"prior": 1.0, "top1": 0.0, "margin": 0.0}) == 0
    assert choose_slot(cache, 0, {"prior": 0.01, "top1": 1.0, "margin": 1.0}) == 1
    assert evaluate_rule(cache, [0], {"prior": 0.01, "top1": 1.0, "margin": 1.0})["top1"] == 1.0
    assert evaluate_rule(cache, [0], "det0")["top1"] == 0.0
