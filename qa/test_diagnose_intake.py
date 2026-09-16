"""pytest для qa/diagnose_intake.py (агент F2, поручение оркестратора после ревью 04):
классификация ошибок по типам, доля fallback/EXIF — чистая логика без cv2/PIL/numpy (их
нет в qa/.venv). `is_label_detector_fallback`/`exif_orientation_tag` (реальные cv2/PIL
вызовы) — не юнит-тест здесь, а факт прогона `packages/cv/.venv/bin/python
qa/diagnose_intake.py` против реальных фото, задокументированный в reports/f-report.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import diagnose_intake as di

# ----------------------------------------------------------------------------------
# is_same_near_dup_family / classify_error
# ----------------------------------------------------------------------------------

_GROUPS = [frozenset({"a-2024", "a-2025"}), frozenset({"b-1", "b-2", "b-3"})]


def test_is_same_near_dup_family_true_for_pair_in_same_group():
    assert di.is_same_near_dup_family("a-2024", "a-2025", _GROUPS) is True


def test_is_same_near_dup_family_false_for_different_groups():
    assert di.is_same_near_dup_family("a-2024", "b-1", _GROUPS) is False


def test_is_same_near_dup_family_false_when_slug_in_no_group():
    assert di.is_same_near_dup_family("unrelated-1", "unrelated-2", _GROUPS) is False


def test_classify_error_not_in_catalog_when_predicted_is_none():
    result = di.classify_error("a-2024", None, near_dup_groups=_GROUPS, query_fallback=False)
    assert result == "not_in_catalog"


def test_classify_error_near_dup_family_takes_priority_over_fallback():
    # near-dup ДОЛЖЕН объяснить промах, даже если запрос заодно попал в fallback-кроп
    # (докстринг classify_error — умышленный порядок проверок).
    result = di.classify_error("a-2024", "a-2025", near_dup_groups=_GROUPS, query_fallback=True)
    assert result == "near_dup_family"


def test_classify_error_detector_fallback_when_not_near_dup():
    result = di.classify_error("unrelated-1", "unrelated-2", near_dup_groups=_GROUPS, query_fallback=True)
    assert result == "detector_fallback"


def test_classify_error_other_when_neither_near_dup_nor_fallback():
    result = di.classify_error("unrelated-1", "unrelated-2", near_dup_groups=_GROUPS, query_fallback=False)
    assert result == "other"


# ----------------------------------------------------------------------------------
# summarize_error_types
# ----------------------------------------------------------------------------------


def test_summarize_error_types_empty():
    summary = di.summarize_error_types([])
    assert summary == {"n_errors": 0, "by_type": {}, "by_type_pct": {}}


def test_summarize_error_types_counts_and_percentages():
    classified = ["near_dup_family", "near_dup_family", "other", "detector_fallback"]
    summary = di.summarize_error_types(classified)
    assert summary["n_errors"] == 4
    assert summary["by_type"] == {"near_dup_family": 2, "other": 1, "detector_fallback": 1}
    assert summary["by_type_pct"]["near_dup_family"] == 50.0
    assert summary["by_type_pct"]["other"] == 25.0


# ----------------------------------------------------------------------------------
# discover_photo_files
# ----------------------------------------------------------------------------------


def test_discover_photo_files_filters_and_sorts(tmp_path: Path):
    (tmp_path / "b.jpg").write_bytes(b"x")
    (tmp_path / "a.webp").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    files = di.discover_photo_files(tmp_path)
    assert [f.name for f in files] == ["a.webp", "b.jpg"]


# ----------------------------------------------------------------------------------
# run() CLI — с фейковым report.json, без реальных фото/cv2 (photos-dir с пустыми файлами
# только упражняет ветку "нет фото" -> код 2; полноценный happy-path CLI тестируется
# фактическим прогоном под packages/cv/.venv, см. reports/f-report.md)
# ----------------------------------------------------------------------------------


def test_run_returns_2_for_missing_photos_dir(tmp_path: Path):
    assert di.run(["--photos-dir", str(tmp_path / "nope")]) == 2


def test_run_returns_2_for_empty_photos_dir(tmp_path: Path):
    assert di.run(["--photos-dir", str(tmp_path)]) == 2
