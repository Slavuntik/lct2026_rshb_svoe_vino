from __future__ import annotations

import numpy as np

from cv.augment import VIEWS_DEFAULT, prepare_reference, render_synthetic_views


def test_default_view_count_meets_dod(synthetic_bottle_image):
    views = render_synthetic_views(synthetic_bottle_image, seed=0)
    assert len(views) == VIEWS_DEFAULT
    assert VIEWS_DEFAULT >= 20  # DoD: из 1 эталона >= 20 ракурсов


def test_generates_at_least_20_views_when_requested(synthetic_bottle_image):
    views = render_synthetic_views(synthetic_bottle_image, n=20, seed=1)
    assert len(views) == 20


def test_view_shapes_and_dtype_are_valid(synthetic_bottle_image):
    views = render_synthetic_views(synthetic_bottle_image, n=6, seed=2, out_size=256)
    for v in views:
        assert v.shape == (256, 256, 3)
        assert v.dtype == np.uint8
        assert v.min() >= 0 and v.max() <= 255
        # не пустой/не однотонный кадр — рендер+эффекты реально что-то произвели
        assert v.std() > 1.0


def test_deterministic_by_seed(synthetic_bottle_image):
    a = render_synthetic_views(synthetic_bottle_image, n=8, seed=7)
    b = render_synthetic_views(synthetic_bottle_image, n=8, seed=7)
    assert len(a) == len(b)
    for va, vb in zip(a, b):
        assert np.array_equal(va, vb)


def test_different_seed_produces_different_views(synthetic_bottle_image):
    a = render_synthetic_views(synthetic_bottle_image, n=4, seed=1)
    b = render_synthetic_views(synthetic_bottle_image, n=4, seed=2)
    assert not all(np.array_equal(va, vb) for va, vb in zip(a, b))


def test_deterministic_across_repeated_calls_same_process(synthetic_bottle_image):
    """Общий rng.default_rng(seed) не должен зависеть от того, что было вызвано
    раньше в процессе (никакого скрытого глобального состояния)."""
    render_synthetic_views(synthetic_bottle_image, n=3, seed=999)  # "прогрев" — не должен повлиять
    a = render_synthetic_views(synthetic_bottle_image, n=5, seed=3)
    b = render_synthetic_views(synthetic_bottle_image, n=5, seed=3)
    for va, vb in zip(a, b):
        assert np.array_equal(va, vb)


def test_prepare_reference_is_deterministic_and_valid(synthetic_bottle_image):
    a = prepare_reference(synthetic_bottle_image, out_size=200)
    b = prepare_reference(synthetic_bottle_image, out_size=200)
    assert a.shape == (200, 200, 3)
    assert a.dtype == np.uint8
    assert np.array_equal(a, b)
