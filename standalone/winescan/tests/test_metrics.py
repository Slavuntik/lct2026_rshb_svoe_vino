import numpy as np
import pytest

from winescan.eval.metrics import best_threshold, f1_with_abstention, retrieval_summary


def test_f1_without_abstention_equals_accuracy():
    correct = np.array([True, False, True, True])

    result = f1_with_abstention(correct, np.ones(4, bool))

    assert result["precision"] == result["recall"] == result["f1"] == pytest.approx(0.75)


def test_abstaining_on_wrong_answers_raises_f1():
    correct = np.array([True, True, True, False])
    confidence = np.array([0.9, 0.8, 0.7, 0.2])

    best = best_threshold(correct, confidence)

    assert best["threshold"] == pytest.approx(0.7)
    assert best["precision"] == 1.0 and best["recall"] == pytest.approx(0.75)
    assert best["f1"] == pytest.approx(2 * 0.75 / 1.75)


def test_retrieval_summary_ranks():
    summary = retrieval_summary([1, 3, None, 1], confidence=[0.5, 0.4, 0.1, 0.6])

    assert summary["top1_accuracy"] == 0.5
    assert summary["top5_accuracy"] == 0.75
    assert summary["mrr_at_10"] == pytest.approx((1 + 1 / 3 + 0 + 1) / 4)
