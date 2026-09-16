"""qa/tests/test_case_ref_triage.py — тесты чистой (без numpy/torch/cv2) логики
qa/case_ref_triage.py. Основная часть модуля (SigLIP2 zero-shot, cv.normalize
cross-check) требует packages/cv/.venv (torch/transformers/cv2 — их нет в обычном
qa/.venv, см. докстринг модуля) и проверена живым прогоном, не pytest — тот же принцип,
что qa/run_cv_index_baseline.py (см. reports/f3-case-census.md, «Команды воспроизведения»)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import case_ref_triage as crt  # noqa: E402


class TestPercentile:
    def test_median_of_odd_list(self):
        assert crt._percentile([1, 2, 3, 4, 5], 50) == 3

    def test_empty_list_returns_none(self):
        assert crt._percentile([], 50) is None

    def test_p0_and_p100_are_extremes(self):
        vals = [5, 1, 3, 2, 4]
        assert crt._percentile(vals, 0) == 1
        assert crt._percentile(vals, 100) == 5

    def test_single_value(self):
        assert crt._percentile([7.0], 25) == 7.0


class TestUsableClassesConfig:
    def test_usable_classes_are_subset_of_class_prompts(self):
        assert crt.USABLE_CLASSES <= set(crt.CLASS_PROMPTS)

    def test_at_least_one_non_usable_class_exists(self):
        # usability_margin = best usable - best NOT usable требует непустого "не-usable".
        assert set(crt.CLASS_PROMPTS) - crt.USABLE_CLASSES
