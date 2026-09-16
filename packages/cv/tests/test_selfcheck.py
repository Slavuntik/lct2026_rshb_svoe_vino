"""Тесты cv/selfcheck.py, часть G3 (agents/G3-real-index.md п.4): self-match на
БОЕВЫХ эталонах `slug_refs.json`, не на дев-директории — `run_selfcheck` (агент G,
слаг = имя файла) не подходит, у боевых файлов uploads/ произвольные имена, не slug.

`run_selfcheck` (директория, агент G) и его покрытие в test_index.py не трогаем —
только добавляем `run_selfcheck_from_refs` и опциональный параметр `groups` у
`is_acceptable_match` (дефолт сохраняет старое поведение, см. cv/selfcheck.py).
"""
from __future__ import annotations

import pytest

from cv.augment import save_synthetic_views
from cv.encoder import SiglipEncoder
from cv.index import ImageIndex
from cv.selfcheck import is_acceptable_match, run_selfcheck_from_refs
from cv.store import QdrantStore

SMALL_N_VIEWS = 4


def test_is_acceptable_match_default_uses_devfix_near_dup_groups():
    assert is_acceptable_match("aligote-barrel-2025", "aligote-barrel-2024") is True
    assert is_acceptable_match("aligote-barrel-2024", "aligote-barrel-2025") is True
    assert is_acceptable_match("unrelated-slug", "aligote-barrel-2024") is False
    assert is_acceptable_match("aligote-barrel-2024", "aligote-barrel-2024") is True  # точное совпадение


def test_is_acceptable_match_custom_groups_override_default():
    """G3: реальные near-dup семьи каталога (slug_refs.json["families"]) передаются
    параметром, а не подменой глобала — дев-фикстурная пара из чужой группы НЕ должна
    засчитываться, если вызывающий код явно передал СВОИ группы."""
    custom = [frozenset({"family-a-2020", "family-a-2021"})]
    assert is_acceptable_match("family-a-2021", "family-a-2020", groups=custom) is True
    assert is_acceptable_match("aligote-barrel-2025", "aligote-barrel-2024", groups=custom) is False


@pytest.fixture(scope="module")
def shared_encoder() -> SiglipEncoder:
    return SiglipEncoder()


@pytest.fixture(scope="module")
def refs_and_index(tmp_path_factory, shared_encoder, devfix_dir):
    """Имитация боевого refs (slug -> путь к эталону), построенная из дев-фикстур —
    сами файлы devfix уже названы по slug (в отличие от боевого uploads/), но это не
    важно для run_selfcheck_from_refs: она принимает `refs` УЖЕ в виде dict[slug, Path],
    не сканирует директорию сама (это ответственность cv.cli.discover_refs_from_slug_refs_json
    в боевом пути)."""
    by_stem = {p.stem: p for p in sorted(devfix_dir.glob("*.webp"))}
    chosen = dict(list(by_stem.items())[:6])
    if len(chosen) < 6:
        pytest.skip("недостаточно дев-фикстур для теста run_selfcheck_from_refs")

    tmp_dir = tmp_path_factory.mktemp("selfcheck_refs")
    store = QdrantStore(path=tmp_dir / "qdrant")
    index = ImageIndex(
        store=store, encoder=shared_encoder, collection="selfcheck_refs_test", manifest_path=tmp_dir / "manifest.json"
    )

    refs: dict[str, list[str]] = {}
    for slug, path in chosen.items():
        synth_paths = save_synthetic_views(path, tmp_dir / "views", slug, n=SMALL_N_VIEWS, seed=0)
        refs[slug] = [str(path)] + [str(p) for p in synth_paths]
    index.build(refs, version="pytest-selfcheck-refs")
    return chosen, index


def test_run_selfcheck_from_refs_reports_top1_and_top5(refs_and_index):
    chosen, index = refs_and_index
    report = run_selfcheck_from_refs(chosen, index=index, n_views=2, seed=777, top_k=5)

    assert report["sampled_slugs"] == len(chosen)
    assert report["total_views"] == len(chosen) * 2
    assert len(report["details"]) == report["total_views"]
    assert 0.0 <= report["top1_rate"] <= 1.0
    assert 0.0 <= report["top5_rate"] <= 1.0
    # top1 в топ-5 по построению -> top5_rate не может быть ниже top1_rate
    assert report["top5_rate"] >= report["top1_rate"]
    assert report["top5_hits"] >= report["top1_hits"]


def test_run_selfcheck_from_refs_sample_n_is_deterministic_subset(refs_and_index):
    chosen, index = refs_and_index
    assert len(chosen) > 3  # иначе sample_n=3 не сузило бы выборку

    report_a = run_selfcheck_from_refs(chosen, index=index, n_views=1, seed=1, sample_n=3, sample_seed=1)
    report_b = run_selfcheck_from_refs(chosen, index=index, n_views=1, seed=1, sample_n=3, sample_seed=1)
    report_c = run_selfcheck_from_refs(chosen, index=index, n_views=1, seed=1, sample_n=3, sample_seed=2)

    assert report_a["sampled_slugs"] == 3
    slugs_a = sorted({d["slug"] for d in report_a["details"]})
    slugs_b = sorted({d["slug"] for d in report_b["details"]})
    assert len(slugs_a) == 3
    assert slugs_a == slugs_b  # тот же sample_seed -> тот же сэмпл слагов

    slugs_c = sorted({d["slug"] for d in report_c["details"]})
    assert slugs_c != slugs_a or len(chosen) <= 3  # другой sample_seed -> обычно другой сэмпл


def test_run_selfcheck_from_refs_splits_by_fallback_ref_quality(refs_and_index):
    """Дополнение оркестратора (16.09.2026, п.3): self-match помечает позиции с
    fallback-детектором отдельной строкой (`by_ref_quality`), чтобы шумные эталоны не
    размазывали общую цифру. Тест бьёт слаги на две произвольные "качественные" группы
    (не зависит от реального исхода нормализации — только от бухгалтерии агрегации)."""
    chosen, index = refs_and_index
    slugs = sorted(chosen.keys())
    fallback_set = set(slugs[: len(slugs) // 2])
    assert fallback_set and len(fallback_set) < len(slugs)  # обе группы непустые

    report = run_selfcheck_from_refs(chosen, index=index, n_views=1, seed=555, fallback_slugs=fallback_set)

    assert "by_ref_quality" in report
    breakdown = report["by_ref_quality"]
    assert set(breakdown) == {"clean", "fallback"}
    assert breakdown["clean"]["total"] + breakdown["fallback"]["total"] == report["total_views"]
    assert breakdown["fallback"]["total"] == len(fallback_set) * 1  # n_views=1
    for d in report["details"]:
        assert d["fallback_ref"] == (d["slug"] in fallback_set)


def test_run_selfcheck_from_refs_no_breakdown_when_fallback_slugs_omitted(refs_and_index):
    chosen, index = refs_and_index
    report = run_selfcheck_from_refs(chosen, index=index, n_views=1, seed=555)
    assert "by_ref_quality" not in report
    assert all(d["fallback_ref"] is False for d in report["details"])
