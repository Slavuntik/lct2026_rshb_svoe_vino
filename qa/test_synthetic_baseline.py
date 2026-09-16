"""pytest для генератора синтетических «полевых» фото (`gen_synthetic_scan_photos.py`) и
адаптера CV-индекса (`run_cv_index_baseline.py`) — агент F2, продолжение зоны F: синтетический
end-to-end смоук сканера (case.md + contracts/image-scan.md v0.4.2).

Тестируется ЧИСТАЯ логика (планирование фото, детерминизм seed, Predictor-адаптер на
фейковом индексе) БЕЗ реальных torch/opencv/numpy — их нет в qa/.venv (qa/requirements.txt:
только pytest/pyyaml/playwright), и оба модуля намеренно откладывают тяжёлые `cv.*`-импорты
внутрь `main()`, чтобы оставаться импортируемыми (и тестируемыми) здесь. Сам рендеринг
(`cv.augment.render_synthetic_views`) и реальный `ImageIndex` — не юнит-тест, а факт одного
полного прогона `packages/cv/.venv/bin/python qa/run_cv_index_baseline.py`, задокументированный
в reports/f-report.md и qa/scan-eval-runs/synthetic-cv-index-baseline/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import gen_synthetic_scan_photos as gen
import run_cv_index_baseline as rcb
import scan_eval as se

# ----------------------------------------------------------------------------------
# discover_devfix_slugs
# ----------------------------------------------------------------------------------


def test_discover_devfix_slugs_sorts_and_filters(tmp_path: Path):
    (tmp_path / "b-wine.webp").write_bytes(b"x")
    (tmp_path / "a-wine.jpg").write_bytes(b"x")
    (tmp_path / "manifest.json").write_text("{}")  # не картинка — игнорируется
    (tmp_path / "c-wine.WEBP").write_bytes(b"x")  # регистр расширения не важен

    assert gen.discover_devfix_slugs(tmp_path) == ["a-wine", "b-wine", "c-wine"]


# ----------------------------------------------------------------------------------
# _stable_seed_for
# ----------------------------------------------------------------------------------


def test_stable_seed_is_deterministic_across_calls():
    assert gen._stable_seed_for("aligote-barrel-2024", 1) == gen._stable_seed_for("aligote-barrel-2024", 1)


def test_stable_seed_differs_by_slug_and_view_index():
    seeds = {
        gen._stable_seed_for("slug-a", 1),
        gen._stable_seed_for("slug-b", 1),
        gen._stable_seed_for("slug-a", 2),
    }
    assert len(seeds) == 3  # ни коллизии по slug, ни по view_index


def test_stable_seed_distinct_from_build_and_selfcheck_seeds():
    # Докстринг модуля: домен полевых seed'ов НЕ пересекается с build-seed индекса (0,
    # cv/config.py::AUGMENT_SEED_DEFAULT) и с holdout-offset селфчека G (9973,
    # cv/selfcheck.py::HOLDOUT_SEED_OFFSET) — иначе "полевое" фото рисковало бы совпасть
    # с ракурсом, уже лежащим в индексе, и self-match стал бы тривиальным.
    for view_index in (1, 2):
        for slug in ("aligote-barrel-2024", "kaberne-sovinon"):
            seed = gen._stable_seed_for(slug, view_index)
            assert seed not in (0, 9973)
            assert seed >= gen.FIELD_SEED_BASE


# ----------------------------------------------------------------------------------
# build_generation_plan
# ----------------------------------------------------------------------------------

# 56 фейковых slug'ов, включая настоящие имена EXTRA_VIEW_SLUGS (near-dup пара + третий) —
# так тест не зависит от диска (packages/cv/devfix), но всё равно упражняет ветку
# "второй ракурс для уже отобранного слага".
_FAKE_56_SLUGS = sorted(
    [f"wine-{i:02d}" for i in range(53)] + ["aligote-barrel-2024", "aligote-barrel-2025", "kaberne-sovinon"]
)


def test_build_generation_plan_total_within_task_range():
    plan = gen.build_generation_plan(_FAKE_56_SLUGS)
    assert 30 <= len(plan) <= 50  # диапазон задания F2: 30-50 «полевых» фото
    assert len(plan) == gen.TOTAL_EXPECTED


def test_build_generation_plan_no_duplicate_slug_view_pairs():
    plan = gen.build_generation_plan(_FAKE_56_SLUGS)
    assert len(plan) == len(set(plan))


def test_build_generation_plan_is_deterministic():
    assert gen.build_generation_plan(_FAKE_56_SLUGS) == gen.build_generation_plan(list(_FAKE_56_SLUGS))


def test_build_generation_plan_stresses_near_dup_pair_with_two_views_each():
    plan = gen.build_generation_plan(_FAKE_56_SLUGS)
    views_2024 = sorted(v for slug, v in plan if slug == "aligote-barrel-2024")
    views_2025 = sorted(v for slug, v in plan if slug == "aligote-barrel-2025")
    assert views_2024 == [1, 2]
    assert views_2025 == [1, 2]


def test_build_generation_plan_includes_real_near_dup_pair_from_disk():
    real_slugs = gen.discover_devfix_slugs(gen._DEVFIX_DIR)
    if len(real_slugs) < gen.N_BASE_SLUGS:
        pytest.skip("packages/cv/devfix недоступен в этом окружении")
    plan = gen.build_generation_plan(real_slugs)
    assert len(plan) == gen.TOTAL_EXPECTED
    slugs_in_plan = {slug for slug, _ in plan}
    assert {"aligote-barrel-2024", "aligote-barrel-2025", "kaberne-sovinon"} <= slugs_in_plan


def test_build_generation_plan_skips_unknown_extra_slug(monkeypatch):
    monkeypatch.setattr(gen, "EXTRA_VIEW_SLUGS", ["not-a-real-slug"])
    plan = gen.build_generation_plan(_FAKE_56_SLUGS)
    assert all(slug != "not-a-real-slug" for slug, _ in plan)
    assert len(plan) == gen.N_BASE_SLUGS


# ----------------------------------------------------------------------------------
# run_cv_index_baseline: _parse_build_summary
# ----------------------------------------------------------------------------------


def test_parse_build_summary_valid_json():
    stdout = '{"positions": 56, "views_per_position": 25, "version": "v1", "build_s": 12.3}\n'
    assert rcb._parse_build_summary(stdout, fallback_version="fallback") == {
        "positions": 56,
        "views_per_position": 25,
        "version": "v1",
        "build_s": 12.3,
    }


def test_parse_build_summary_falls_back_on_garbage():
    summary = rcb._parse_build_summary("не json вовсе", fallback_version="fallback-v")
    assert summary["version"] == "fallback-v"
    assert summary["parse_error"] is True


# ----------------------------------------------------------------------------------
# CvIndexPredictor — фейковый индекс, без реального ImageIndex/torch
# ----------------------------------------------------------------------------------


class _FakeMatch:
    def __init__(self, slug: str, score: float, gap: float | None):
        self.slug = slug
        self.score = score
        self.gap = gap


class _FakeIndexReturning:
    def __init__(self, matches: list[_FakeMatch]):
        self._matches = matches
        self.last_top_k: int | None = None

    def search(self, image_bytes: bytes, top_k: int = 5):  # noqa: ARG002
        self.last_top_k = top_k
        return self._matches


class _FakeIndexRaising:
    def search(self, image_bytes: bytes, top_k: int = 5):  # noqa: ARG002
        raise ValueError("битый файл")


def test_cv_index_predictor_maps_matches_to_prediction():
    matches = [
        _FakeMatch("wine-a", 0.91, 0.12),
        _FakeMatch("wine-b", 0.79, None),
        _FakeMatch("wine-c", 0.55, None),
    ]
    pred = rcb.CvIndexPredictor(_FakeIndexReturning(matches), top_k=5).predict(b"fake-bytes", "photo.jpg")

    assert pred.top1_slug == "wine-a"
    assert pred.top5_slugs == ["wine-a", "wine-b", "wine-c"]
    assert pred.top1_score == pytest.approx(0.91)
    assert pred.gap == pytest.approx(0.12)
    assert pred.error is None
    assert pred.latency_ms >= 0.0


def test_cv_index_predictor_passes_through_top_k():
    fake_index = _FakeIndexReturning([])
    rcb.CvIndexPredictor(fake_index, top_k=3).predict(b"x", "p.jpg")
    assert fake_index.last_top_k == 3


def test_cv_index_predictor_empty_matches_is_a_miss_not_an_error():
    pred = rcb.CvIndexPredictor(_FakeIndexReturning([])).predict(b"x", "p.jpg")
    assert pred.top1_slug is None
    assert pred.top5_slugs == []
    assert pred.error is None  # честный "не нашли", не ошибка транспорта (тот же принцип,
    # что FlatApiPredictor/RichApiPredictor применяют к пустому slug — см. scan_eval.py)


def test_cv_index_predictor_value_error_becomes_transport_error():
    pred = rcb.CvIndexPredictor(_FakeIndexRaising()).predict(b"broken", "p.jpg")
    assert pred.top1_slug is None
    assert pred.error is not None
    assert "битый файл" in pred.error


def test_cv_index_predictor_runs_through_real_scan_eval_run_eval(tmp_path: Path):
    # run_eval() принимает Predictor по структурной типизации (Protocol) — проверим, что
    # CvIndexPredictor реально прогоняется настоящим scan_eval.run_eval(), не только вызовом
    # .predict() напрямую.
    matches = [_FakeMatch("wine-a", 0.9, 0.2)]
    predictor = rcb.CvIndexPredictor(_FakeIndexReturning(matches))
    photo = tmp_path / "p1.jpg"
    photo.write_bytes(b"stand-in bytes; fake index ignores content")
    items = [se.EvalItem(photo_id="p1.jpg", path=photo, true_slug="wine-a")]

    records = se.run_eval(items, predictor)

    assert len(records) == 1
    assert records[0].hit_top1 is True


# ----------------------------------------------------------------------------------
# Разметка реального прогона (если уже сгенерирована) — не полагаемся на порядок запуска
# ----------------------------------------------------------------------------------


def test_synthetic_labels_csv_loads_cleanly_if_present():
    labels_csv = gen._LABELS_CSV
    photos_dir = gen._PHOTOS_DIR
    if not labels_csv.is_file():
        pytest.skip("qa/synthetic/labels.csv ещё не сгенерирован (qa/gen_synthetic_scan_photos.py)")
    items, warnings = se.load_eval_set(photos_dir, labels_csv)
    assert warnings == []
    assert 30 <= len(items) <= 50
    assert len(items) == len(set(it.photo_id for it in items))
