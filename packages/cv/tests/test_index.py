"""Тесты ImageIndex — контракт + DoD брифа:
- self-match >= 90% на дев-фикстурах (свежие holdout-ракурсы, другой seed, чем build);
- near-dup пара (aligote-barrel-2024/2025) — в одной группе кандидатов;
- add() без ребилда — новая позиция ищется сразу;
- search() на битом файле -> ValueError, не 500-полуфабрикат.

Небольшой n (SMALL_N_VIEWS) и малая выборка слагов — реальный энкодер SigLIP2 не
бесплатен по времени; полная self-check сводка на всех дев-фикстурах — `cv selfcheck`
(см. reports/g-report.md), здесь — представительная выборка, включающая обязательную
near-dup пару, ради разумного времени прогона pytest.
"""
from __future__ import annotations

import pytest

from cv.augment import render_synthetic_views, save_synthetic_views
from cv.encoder import SiglipEncoder
from cv.imageio import encode_jpeg, load_image_file
from cv.index import ImageIndex, Match, _cluster_by_score, _gaps_to_next_group
from cv.selfcheck import is_acceptable_match
from cv.store import QdrantStore

SMALL_N_VIEWS = 6
HOLDOUT_SEED = 424242


@pytest.fixture(scope="module")
def shared_encoder() -> SiglipEncoder:
    return SiglipEncoder()


@pytest.fixture(scope="module")
def sample_refs(devfix_dir) -> dict[str, "Path"]:  # noqa: F821 - строковая аннотация, Path из pathlib ниже
    from pathlib import Path

    by_stem = {p.stem: p for p in devfix_dir.glob("*.webp")}
    required = [s for s in ("aligote-barrel-2024", "aligote-barrel-2025") if s in by_stem]
    if len(required) < 2:
        pytest.skip("near-dup фикстуры (aligote-barrel-2024/2025) не скачаны")
    others = sorted(s for s in by_stem if s not in required)[:6]
    chosen = required + others
    return {s: by_stem[s] for s in chosen}


@pytest.fixture(scope="module")
def built_index(tmp_path_factory, shared_encoder, sample_refs):
    tmp_dir = tmp_path_factory.mktemp("cv_index")
    store = QdrantStore(path=tmp_dir / "qdrant")
    # manifest_path явный (не config.MANIFEST_PATH по умолчанию) — иначе несколько
    # ImageIndex в одном процессе (тут и в test_index_version_is_none_before_any_build)
    # делили бы один файл манифеста в packages/cv/data/.
    index = ImageIndex(
        store=store, encoder=shared_encoder, collection="test_views", manifest_path=tmp_dir / "manifest.json"
    )

    views_dir = tmp_dir / "views"
    refs = {}
    for slug, path in sample_refs.items():
        synth_paths = save_synthetic_views(path, views_dir, slug, n=SMALL_N_VIEWS, seed=0)
        refs[slug] = [str(path)] + [str(p) for p in synth_paths]
    index.build(refs, version="pytest")
    return index


def test_manifest_written_with_version(built_index):
    import json

    manifest = json.loads(built_index._manifest_path.read_text())
    assert manifest["version"] == "pytest"
    assert manifest["positions"] == 8
    assert manifest["vectors"] == 8 * (SMALL_N_VIEWS + 1)


def test_index_version_property_reads_manifest(built_index):
    """Ревью 04, блокер 5: /v1/metrics/scan у B читает эту property, не env-плейсхолдер."""
    assert built_index.index_version == "pytest"


def test_index_version_is_none_before_any_build(tmp_path, shared_encoder):
    fresh = ImageIndex(
        store=QdrantStore(path=tmp_path / "qdrant"),
        encoder=shared_encoder,
        collection="never_built",
        manifest_path=tmp_path / "manifest.json",
    )
    assert fresh.index_version is None


def test_two_indices_in_one_process_have_independent_index_version(tmp_path_factory, shared_encoder, sample_refs):
    """Регресс-тест (находка B при интеграции с packages/cv, ревью 04+): `config.MANIFEST_PATH`
    резолвится как модульная константа, замороженная при ПЕРВОМ импорте `cv.config` — не видит
    env/аргументы, выставленные позже. Раньше это было единственным дефолтом `manifest_path`,
    так что второй `ImageIndex` в том же процессе (свой `store`, но БЕЗ явного `manifest_path`)
    читал манифест ПЕРВОГО. Дефолт теперь выводится из `self.store.path` (см. `cv/index.py`,
    `ImageIndex.__init__`) — у разных store разные манифесты сами по себе, без необходимости
    явно передавать `manifest_path` каждый раз. Намеренно НЕ передаём `manifest_path` ниже —
    именно дефолт и есть то, что чинили."""
    slug, path = next(iter(sample_refs.items()))

    def _build_one(version: str) -> ImageIndex:
        tmp_dir = tmp_path_factory.mktemp("cv_index_two")
        store = QdrantStore(path=tmp_dir / "qdrant")
        index = ImageIndex(store=store, encoder=shared_encoder, collection="two_idx_test")
        synth_paths = save_synthetic_views(path, tmp_dir / "views", slug, n=2, seed=0)
        index.build({slug: [str(path)] + [str(p) for p in synth_paths]}, version=version)
        return index

    index_a = _build_one("version-a")
    index_b = _build_one("version-b")

    assert index_a._manifest_path != index_b._manifest_path
    assert index_a.index_version == "version-a"
    assert index_b.index_version == "version-b"


def test_self_match_top1_rate_meets_dod(built_index, sample_refs):
    """DoD: аугментация эталона находит свой slug top-1 на индексе фикстур >= 90%.

    `is_acceptable_match` засчитывает top-1 внутри известной near-dup группы
    (aligote-barrel-2024/2025 — буквально одна и та же исходная фотография) как
    успех: контракт прямо возлагает их различение на OCR-верификатор, не на CV
    (см. cv/selfcheck.py, докстринг NEAR_DUP_GROUPS) — без этой поправки метрика
    штрафовала бы за информацию, которой в кадре структурно нет.
    """
    total = hits = 0
    for slug, path in sample_refs.items():
        arr = load_image_file(str(path))
        holdout_views = render_synthetic_views(arr, n=3, seed=HOLDOUT_SEED)
        for view in holdout_views:
            total += 1
            matches = built_index.search(encode_jpeg(view), top_k=5)
            top1 = matches[0].slug if matches else None
            if is_acceptable_match(top1, slug):
                hits += 1
    rate = hits / total
    assert rate >= 0.9, f"self-match {hits}/{total} = {rate:.1%} < 90% DoD"


def test_near_dup_pair_lands_in_same_candidate_group(built_index, sample_refs):
    """Пара 2024/2025 — одна этикетка (иногда буквально один файл), разный год.
    CV не обязан их различать (это работа OCR-верификатора, не моя зона) — контракт
    требует, чтобы обе оказались среди кандидатов top-K, когда запрос — одна из них."""
    query_bytes = sample_refs["aligote-barrel-2024"].read_bytes()
    matches = built_index.search(query_bytes, top_k=5)
    slugs = [m.slug for m in matches]
    assert "aligote-barrel-2024" in slugs
    assert "aligote-barrel-2025" in slugs


def test_add_without_rebuild_is_searchable_immediately(built_index, devfix_dir, sample_refs):
    all_imgs = {p.stem: p for p in devfix_dir.glob("*.webp")}
    candidates = sorted(s for s in all_imgs if s not in sample_refs)
    assert candidates, "нет свободной дев-фикстуры для теста add()"
    new_slug = candidates[0]
    new_path = all_imgs[new_slug]

    before = built_index.store.count(built_index.collection)
    built_index.add(new_slug, [new_path.read_bytes()])
    after = built_index.store.count(built_index.collection)
    # add() не принимает n_views (сигнатура — по контракту): всегда использует
    # config.AUGMENT_VIEWS_PER_REF, а не SMALL_N_VIEWS, которым build() засеян
    # для скорости pytest в фикстуре built_index выше — это два разных параметра.
    from cv import config as cv_config

    assert after == before + (cv_config.AUGMENT_VIEWS_PER_REF + 1)  # без пересборки — только добавили точки

    matches = built_index.search(new_path.read_bytes(), top_k=5)
    assert matches and matches[0].slug == new_slug


def test_search_on_corrupt_file_raises_value_error(tmp_path):
    """Контракт: битый файл -> ValueError, не 500-полуфабрикат. decode_image() падает
    ДО обращения к энкодеру/Qdrant — тест быстрый вне зависимости от их состояния."""
    store = QdrantStore(path=tmp_path / "qdrant")
    index = ImageIndex(store=store, encoder=SiglipEncoder(), collection="corrupt_test")
    with pytest.raises(ValueError):
        index.search(b"not an image, just some garbage bytes 0123456789")
    with pytest.raises(ValueError):
        index.embed(b"")


def test_search_top_k_respected(built_index, sample_refs):
    # Не полагаемся на порядок выполнения других тестов в модуле (могли уже
    # добавить позицию через add()) — проверяем только инварианты top_k и
    # отсутствия дублей позиций, не точное число.
    query_bytes = sample_refs["aligote-barrel-2024"].read_bytes()
    matches = built_index.search(query_bytes, top_k=3)
    assert len(matches) <= 3
    matches_more = built_index.search(query_bytes, top_k=100)
    assert len(matches_more) == len(set(m.slug for m in matches_more))  # без дублей позиций
    assert len(sample_refs) <= len(matches_more) <= len(sample_refs) + 5


# --- юниты на чистую арифметику группировки/gap (без энкодера, без сети) -----------


def test_cluster_by_score_chains_close_neighbors():
    scores = [0.90, 0.89, 0.60, 0.59, 0.10]
    groups = _cluster_by_score(scores, epsilon=0.03)
    assert groups == [0, 0, 1, 1, 2]


def test_gaps_to_next_group_skips_same_group_neighbors():
    scores = [0.90, 0.89, 0.60, 0.10]  # первые два — одна группа (near-dup-подобные)
    gaps = _gaps_to_next_group(scores, epsilon=0.03)
    assert gaps[0] == pytest.approx(0.90 - 0.60)  # НЕ 0.90-0.89
    assert gaps[1] == pytest.approx(0.89 - 0.60)
    assert gaps[2] == pytest.approx(0.60 - 0.10)
    assert gaps[3] is None  # последний элемент — дальше группы нет


def test_match_is_plain_dataclass_per_contract():
    m = Match(slug="x", score=0.5, gap=0.1, view="real")
    assert (m.slug, m.score, m.gap, m.view) == ("x", 0.5, 0.1, "real")
