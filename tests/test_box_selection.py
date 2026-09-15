import pytest

from winescan.search.box_selection import PRIOR_ONLY, choose_box


def test_prior_only_keeps_first_box_by_weight():
    assert choose_box([0.5, 0.9, 0.2], [0.9, 0.7, 0.95], [0.1, 0.0, 0.2], PRIOR_ONLY) == 1


def test_retrieval_confidence_can_override_weaker_prior():
    rule = {"prior": 0.02, "top1": 1.0, "margin": 3.0}

    # вторая рамка чуть менее вероятна априори, но поиск по ней уверенный
    assert choose_box([1.0, 0.8], [0.70, 0.84], [0.001, 0.03], rule) == 1


def test_strong_prior_wins_over_small_retrieval_gain():
    rule = {"prior": 0.1, "top1": 1.0, "margin": 0.0}

    # сосед на краю (prior в 20 раз меньше) не должен выигрывать за счёт +0,02 скора
    assert choose_box([1.0, 0.05], [0.80, 0.82], [0.02, 0.02], rule) == 0


def test_empty_boxes_raise():
    with pytest.raises(ValueError):
        choose_box([], [], [])
