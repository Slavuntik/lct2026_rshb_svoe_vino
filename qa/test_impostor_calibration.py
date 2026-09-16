"""pytest для impostor-холдаута и калибровки CV_CONFIDENT_SCORE_THRESHOLD (агент F2,
поручение оркестратора после ревью 04) — `qa/gen_impostor_photos.py` +
`qa/calibrate_not_in_catalog_threshold.py`.

Тестируется ЧИСТАЯ логика (отбор held-out слагов, построение эталонов калибровочного
индекса, арифметика FPR/FNR/рекомендации порога) без torch/opencv/numpy — их нет в
qa/.venv. Генерация фейковых текстур (PIL/numpy) — отдельный тест, помечен
`pytest.importorskip`, пропускается под обычным qa/.venv и проходит под packages/cv/.venv.
Сам реальный прогон (построение калибровочного индекса + скоринг) — не юнит-тест, а факт
одного полного прогона `packages/cv/.venv/bin/python qa/calibrate_not_in_catalog_threshold.py`,
задокументированный в reports/f-report.md и qa/scan-eval-runs/not-in-catalog-calibration/.
"""

from __future__ import annotations

import pytest

import calibrate_not_in_catalog_threshold as cnc
import gen_impostor_photos as gip

# ----------------------------------------------------------------------------------
# pick_holdout_slugs / build_calibration_refs
# ----------------------------------------------------------------------------------

_FAKE_56_SLUGS = sorted(f"wine-{i:02d}" for i in range(56))


def test_pick_holdout_slugs_returns_expected_size():
    holdout = gip.pick_holdout_slugs(_FAKE_56_SLUGS, n_base=42, n_holdout=10)
    assert len(holdout) == 10


def test_pick_holdout_slugs_is_disjoint_from_positive_base():
    # Позитивный набор F2 использует all_slugs[:N_BASE_SLUGS] (gen_synthetic_scan_photos.
    # build_generation_plan) — held-out обязан начинаться СРАЗУ после этой границы, нулевое
    # пересечение по построению, не по совпадению.
    base = set(_FAKE_56_SLUGS[:42])
    holdout = set(gip.pick_holdout_slugs(_FAKE_56_SLUGS, n_base=42, n_holdout=10))
    assert base.isdisjoint(holdout)
    assert len(holdout) == 10


def test_pick_holdout_slugs_is_deterministic():
    a = gip.pick_holdout_slugs(_FAKE_56_SLUGS)
    b = gip.pick_holdout_slugs(list(_FAKE_56_SLUGS))
    assert a == b


def test_pick_holdout_slugs_uses_real_n_base_slugs_constant():
    # Импортирована из gen_synthetic_scan_photos, не задублирована локальной константой —
    # если позитивный набор когда-то расширят, held-out не должен молча начать
    # пересекаться с ним.
    import gen_synthetic_scan_photos as gen

    assert gip.N_BASE_SLUGS == gen.N_BASE_SLUGS


def test_build_calibration_refs_excludes_holdout():
    holdout = gip.pick_holdout_slugs(_FAKE_56_SLUGS, n_base=42, n_holdout=10)
    refs = gip.build_calibration_refs(_FAKE_56_SLUGS, holdout)
    assert len(refs) == 56 - 10
    assert set(refs).isdisjoint(set(holdout))


def test_build_calibration_refs_preserves_input_order():
    # Немаршрутизированный (не отсортированный) вход — проверяем, что функция именно
    # ФИЛЬТРУЕТ по месту, а не молча пересортировывает результат.
    slugs = ["c", "a", "b", "d"]
    refs = gip.build_calibration_refs(slugs, ["a"])
    assert refs == ["c", "b", "d"]


def test_pick_holdout_slugs_on_real_devfix_has_no_overlap_with_positive_set():
    real_slugs = gip.discover_devfix_slugs(gip._DEVFIX_DIR)
    if len(real_slugs) < gip.N_BASE_SLUGS + gip.HOLDOUT_SIZE:
        pytest.skip("packages/cv/devfix недоступен в этом окружении")
    holdout = gip.pick_holdout_slugs(real_slugs)
    assert len(holdout) == gip.HOLDOUT_SIZE
    # near-dup пара и доп.slug позитивного набора не должны утечь в impostor-holdout
    assert "aligote-barrel-2024" not in holdout
    assert "aligote-barrel-2025" not in holdout
    assert "kaberne-sovinon" not in holdout


# ----------------------------------------------------------------------------------
# generate_fake_label_texture — требует PIL/numpy (нет в qa/.venv, есть в packages/cv/.venv)
# ----------------------------------------------------------------------------------


def test_generate_fake_label_texture_deterministic_shape_and_dtype():
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    a = gip.generate_fake_label_texture(seed=42)
    b = gip.generate_fake_label_texture(seed=42)
    assert isinstance(a, np.ndarray)
    assert a.dtype == np.uint8
    assert a.ndim == 3 and a.shape[2] == 3
    assert (a == b).all()  # детерминизм по seed


def test_generate_fake_label_texture_differs_by_seed():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    a = gip.generate_fake_label_texture(seed=1)
    b = gip.generate_fake_label_texture(seed=2)
    assert not (a == b).all()


# ----------------------------------------------------------------------------------
# compute_fpr / compute_fnr — арифметика
# ----------------------------------------------------------------------------------


def test_compute_fnr_empty_returns_zero():
    assert cnc.compute_fnr([], 0.5) == 0.0


def test_compute_fpr_empty_returns_zero():
    assert cnc.compute_fpr([], 0.5) == 0.0


def test_compute_fnr_hand_computed():
    # [0.9, 0.85, 0.6, 0.95], threshold=0.7 -> ниже порога только 0.6 -> 1/4
    assert cnc.compute_fnr([0.9, 0.85, 0.6, 0.95], 0.7) == pytest.approx(0.25)


def test_compute_fpr_hand_computed():
    # [0.9, 0.4, 0.3, 0.75], threshold=0.7 -> >= порога: 0.9 и 0.75 -> 2/4
    assert cnc.compute_fpr([0.9, 0.4, 0.3, 0.75], 0.7) == pytest.approx(0.5)


def test_compute_fpr_boundary_is_inclusive():
    # top1_score == threshold -> confident=True в apps/api (score >= threshold) -> FP
    assert cnc.compute_fpr([0.7], 0.7) == 1.0


def test_compute_fnr_boundary_is_exclusive():
    # top1_score == threshold -> НЕ FNR (не строго меньше threshold)
    assert cnc.compute_fnr([0.7], 0.7) == 0.0


# ----------------------------------------------------------------------------------
# sweep_thresholds
# ----------------------------------------------------------------------------------


def test_sweep_thresholds_returns_one_row_per_threshold():
    rows = cnc.sweep_thresholds([0.9, 0.8], {"holdout_real": [0.5, 0.6]}, [0.5, 0.6, 0.7])
    assert len(rows) == 3
    assert [r["threshold"] for r in rows] == [0.5, 0.6, 0.7]


def test_sweep_thresholds_fnr_is_monotonically_nondecreasing():
    positive = [0.95, 0.9, 0.85, 0.7, 0.6, 0.5, 0.4]
    rows = cnc.sweep_thresholds(positive, {"src": [0.3]}, [round(0.3 + 0.1 * i, 2) for i in range(8)])
    fnrs = [r["fnr_positive"] for r in rows]
    assert fnrs == sorted(fnrs)  # выше порог -> больше (или столько же) ложных not_in_catalog


def test_sweep_thresholds_fpr_combined_is_monotonically_nonincreasing():
    impostors = [0.95, 0.9, 0.85, 0.7, 0.6, 0.5, 0.4]
    rows = cnc.sweep_thresholds([0.99], {"src": impostors}, [round(0.3 + 0.1 * i, 2) for i in range(8)])
    fprs = [r["fpr_combined"] for r in rows]
    assert fprs == sorted(fprs, reverse=True)  # выше порог -> меньше (или столько же) ложных confident


def test_sweep_thresholds_per_source_breakdown_is_independent():
    rows = cnc.sweep_thresholds(
        [0.9], {"holdout_real": [0.9, 0.9], "fake_synthetic": [0.1, 0.1]}, [0.5]
    )
    row = rows[0]
    assert row["fpr_holdout_real"] == 1.0  # оба held-out прошли бы порог 0.5 как confident
    assert row["fpr_fake_synthetic"] == 0.0  # фейковые этикетки честно ниже порога
    assert row["fpr_combined"] == pytest.approx(0.5)  # 2 из 4 суммарно


# ----------------------------------------------------------------------------------
# recommend_threshold
# ----------------------------------------------------------------------------------

_HAND_ROWS = [
    {"threshold": 0.5, "fnr_positive": 0.1, "fpr_combined": 0.30},
    {"threshold": 0.6, "fnr_positive": 0.2, "fpr_combined": 0.10},
    {"threshold": 0.7, "fnr_positive": 0.3, "fpr_combined": 0.04},
    {"threshold": 0.8, "fnr_positive": 0.5, "fpr_combined": 0.01},
]


def test_recommend_threshold_picks_minimal_threshold_meeting_fpr_cap():
    rec = cnc.recommend_threshold(_HAND_ROWS, max_fpr=0.05)
    assert rec["recommended"]["threshold"] == 0.7  # первый (минимальный), где fpr<=0.05


def test_recommend_threshold_returns_none_when_no_threshold_meets_cap():
    rec = cnc.recommend_threshold(_HAND_ROWS, max_fpr=0.005)  # строже, чем любой ряд
    assert rec["recommended"] is None
    assert "не даёт" in rec["recommended_rationale"]


def test_recommend_threshold_equal_error_rate_point():
    rec = cnc.recommend_threshold(_HAND_ROWS, max_fpr=0.05)
    # |fpr-fnr|: 0.5->0.20, 0.6->0.10, 0.7->0.26, 0.8->0.49 -> минимум на 0.6
    assert rec["equal_error_rate_point"]["threshold"] == 0.6


def test_recommend_threshold_rationale_mentions_case_priority_when_found():
    rec = cnc.recommend_threshold(_HAND_ROWS, max_fpr=0.05)
    assert "not_in_catalog" in rec["recommended_rationale"] or "50/100" in rec["recommended_rationale"]
