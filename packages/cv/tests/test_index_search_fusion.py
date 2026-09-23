"""Тесты `ImageIndex.search_fusion()`/`_exact_scores_for_slugs()` (agents/
G7-text-fusion.md, CV_FUSION) — максимум по двум входам запроса (нормализованный
кроп И весь кадр) + точный фильтрованный добор slug'ов вне ANN-топа.

Векторы — ручные (не настоящий SigLIP2): энкодер подменён на маленький дубль,
различающий "нормализованный кроп" (canonical 448x448, `cv.normalize.
NORM_SIZE_DEFAULT`) от "весь кадр как есть" ПО ФОРМЕ массива — детерминированно
и без стоимости реальной модели (та же экономия, что уже применяет `cv.text_
fusion`'s юниты). `ImageIndex.search()`/обычные ANN-тесты на реальном энкодере —
test_index.py, здесь фокус только на НОВОЙ механике слияния.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from cv.imageio import encode_jpeg
from cv.index import ImageIndex, Match
from cv.normalize import NORM_SIZE_DEFAULT
from cv.store import QdrantStore, point_id


class _ShapeKeyedEncoder:
    """`.encode()` игнорирует пиксели — возвращает `norm_vec`, если вход имеет
    канонический размер нормализации (значит это был `normalize_query()`-кроп),
    иначе `raw_vec` (весь кадр как есть, без нормализации) — ровно то различие,
    которое `search_fusion()` обязана эксплуатировать (docstring метода)."""

    def __init__(self, norm_vec: list[float], raw_vec: list[float]):
        self._norm_vec = norm_vec
        self._raw_vec = raw_vec

    def encode(self, image: np.ndarray, use_cache: bool = True) -> list[float]:
        h, w = image.shape[:2]
        if (h, w) == (NORM_SIZE_DEFAULT, NORM_SIZE_DEFAULT):
            return self._norm_vec
        return self._raw_vec

    def encode_batch(self, images: list[np.ndarray], use_cache: bool = True) -> list[list[float]]:
        """agents/H2-rapidocr-multiscale.md п.4: `embed_fusion_query()` кодирует ОДНИМ
        батчем (`SiglipEncoder.encode_batch()`), не N отдельными вызовами `encode()` —
        тот же тривиальный цикл, что настоящий `cv.encoder.SiglipEncoder.encode_batch()`
        (не батчит инференс по-настоящему, просто единая точка вызова)."""
        return [self.encode(im, use_cache=use_cache) for im in images]


def _unit(vec: list[float]) -> list[float]:
    arr = np.asarray(vec, dtype=np.float64)
    return (arr / np.linalg.norm(arr)).tolist()


def _upsert_slug(store: QdrantStore, collection: str, slug: str, vectors: dict[str, list[float]]) -> None:
    """`vectors`: view -> вектор (обычно {"real": [...]}, иногда несколько
    ракурсов на слаг — см. test_exact_scores_takes_max_across_a_slugs_own_views).
    `ensure_collection` — идемпотентно, так несколько слагов можно добавлять в
    ОДНУ коллекцию последовательными вызовами (как test_store.py делает вручную)."""
    dim = len(next(iter(vectors.values())))
    store.ensure_collection(collection, dim)
    ids = [f"{slug}::{view}" for view in vectors]
    vecs = list(vectors.values())
    payloads = [{"slug": slug, "view": view} for view in vectors]
    store.upsert(collection, ids, vecs, payloads)


@pytest.fixture()
def query_image_bytes(synthetic_bottle_image) -> bytes:
    """Байты, декодируемые `imageio.decode_image()` в массив ФОРМЫ, отличной от
    канонического 448x448 (синтетическая фикстура conftest.py — 300x140x3) —
    нужно, чтобы `_ShapeKeyedEncoder` мог различить "сырой кадр" от "нормализованный
    кроп" (тот ВСЕГДА 448x448, см. `cv.normalize.normalize_query`)."""
    return encode_jpeg(synthetic_bottle_image)


# --------------------------------------------------------------------------------------
# search_fusion(): максимум по двум входам (brief п.5)
# --------------------------------------------------------------------------------------


def test_search_fusion_max_combines_norm_and_raw_views(tmp_path, query_image_bytes):
    """Слаг, "видимый" ТОЛЬКО нормализованным кропом, и слаг, "видимый" ТОЛЬКО
    сырым кадром, — оба обязаны получить свой полный скор (max, не среднее и не
    скор только одного из двух запросов)."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "norm-favorite", {"real": norm_vec})
    _upsert_slug(store, "fusion_test", "raw-favorite", {"real": raw_vec})

    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )
    matches = index.search_fusion(query_image_bytes, top_k=5)
    by_slug = {m.slug: m.score for m in matches}
    assert by_slug["norm-favorite"] == pytest.approx(1.0)
    assert by_slug["raw-favorite"] == pytest.approx(1.0)


def test_search_fusion_with_precomputed_vectors_equals_plain_call(tmp_path, query_image_bytes):
    """`embed_fusion_query()` + `search_fusion(vectors=...)` (эмбеддинги параллельно с чтением
    текста этикетки) обязаны давать РОВНО тот же результат, что обычный вызов, и не
    кодировать картинку повторно (image=None допустим, когда vectors переданы)."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "norm-favorite", {"real": norm_vec})
    _upsert_slug(store, "fusion_test", "raw-favorite", {"real": _unit([0, 0.6, 0.8])})

    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )
    plain = index.search_fusion(query_image_bytes, top_k=5)
    vectors = index.embed_fusion_query(query_image_bytes)
    assert vectors == (norm_vec, raw_vec)
    reused = index.search_fusion(None, top_k=5, vectors=vectors)
    assert [(m.slug, round(m.score, 6)) for m in reused] == [(m.slug, round(m.score, 6)) for m in plain]
    with pytest.raises(ValueError):
        index.search_fusion(None, top_k=5)


def test_search_fusion_slug_matching_neither_view_scores_low(tmp_path, query_image_bytes):
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    orthogonal = _unit([0, 0, 1])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "favorite", {"real": norm_vec})
    _upsert_slug(store, "fusion_test", "unrelated", {"real": orthogonal})

    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )
    matches = index.search_fusion(query_image_bytes, top_k=5)
    by_slug = {m.slug: m.score for m in matches}
    assert by_slug["unrelated"] == pytest.approx(0.0, abs=1e-6)
    assert matches[0].slug == "favorite"  # ранжирование по убыванию — фаворит первым


def test_search_fusion_extra_slug_missing_from_collection_is_silently_absent(tmp_path, query_image_bytes):
    """brief: слаг без эталона в индексе вообще -> отсутствует на выходе, не
    исключение и не заглушка (та — забота `cv.text_fusion.fuse()`, не эта функция)."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "only-slug", {"real": norm_vec})
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )
    matches = index.search_fusion(query_image_bytes, top_k=5, extra_slugs=["never-indexed-slug"])
    assert "never-indexed-slug" not in {m.slug for m in matches}
    assert {m.slug for m in matches} == {"only-slug"}


def test_search_fusion_on_empty_collection_returns_empty_list(tmp_path, query_image_bytes):
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="never_built",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )
    assert index.search_fusion(query_image_bytes, top_k=5, extra_slugs=["whatever"]) == []


def test_search_fusion_decode_error_raises_value_error(tmp_path):
    store = QdrantStore(path=tmp_path / "qdrant")
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder([1.0], [1.0]), collection="corrupt_fusion_test",
        manifest_path=tmp_path / "manifest.json",
    )
    with pytest.raises(ValueError):
        index.search_fusion(b"not an image, just garbage bytes 0123456789")


def test_search_fusion_recovers_slug_the_natural_ann_overfetch_excludes(tmp_path, query_image_bytes):
    """Коллекция сконструирована так, что естественный ANN (оверфетч=11 на
    `top_k=1`, `max(top_k*CV_SEARCH_OVERFETCH, top_k+10)`, cv/config.py) НЕ
    находит `target` вовсе ни через нормализованный, ни через сырой запрос: 11
    "шумовых" клонов с ИДЕАЛЬНЫМ (1.0) сходством к норм-вектору целиком
    заполняют оверфетч норм-запроса, ещё 11 клонов делают то же для сырого
    запроса — `target` (посредственное, но ненулевое сходство к ОБОИМ осям)
    проигрывает обоим кластерам по всем 11 местам разом. Явный запрос
    `extra_slugs=["target"]` обязан достать его ТОЧНЫМ фильтрованным скором
    (`_exact_scores_for_slugs`), не приближением через ANN-топ."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    for i in range(11):
        _upsert_slug(store, "fusion_overfetch_test", f"norm-noise-{i:02d}", {"real": norm_vec})
    for i in range(11):
        _upsert_slug(store, "fusion_overfetch_test", f"raw-noise-{i:02d}", {"real": raw_vec})
    target_vec = _unit([0.5, 0.5, 0.1])
    _upsert_slug(store, "fusion_overfetch_test", "target", {"real": target_vec})

    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_overfetch_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
    )

    without_extra = index.search_fusion(query_image_bytes, top_k=1)
    assert "target" not in {m.slug for m in without_extra}, (
        "предпосылка теста: естественный ANN не должен видеть target вовсе (оба кластера шума его вытесняют)"
    )

    with_extra = index.search_fusion(query_image_bytes, top_k=1, extra_slugs=["target"])
    target_match = next(m for m in with_extra if m.slug == "target")
    expected = max(float(np.dot(target_vec, norm_vec)), float(np.dot(target_vec, raw_vec)))
    assert target_match.score == pytest.approx(expected, abs=1e-5)


def test_search_fusion_gap_falls_back_to_epsilon_without_families_file(tmp_path, query_image_bytes):
    """Без переписи (`families_json` указывает в никуда) — тот же эпсилон-фолбэк,
    что и `search()` (см. `cv/index.py::_gaps_to_next_group`, `CV_GROUP_EPSILON`)."""
    from cv import config as cv_config

    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_gap_test", "top", {"real": norm_vec})
    _upsert_slug(store, "fusion_gap_test", "far", {"real": _unit([0, 0, 1])})
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_gap_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-such-families.json",
    )
    matches = index.search_fusion(query_image_bytes, top_k=5)
    top = next(m for m in matches if m.slug == "top")
    far = next(m for m in matches if m.slug == "far")
    assert top.gap == pytest.approx(top.score - far.score)
    assert top.score - far.score > cv_config.GROUP_EPSILON  # разные "группы" — gap не None


def test_search_fusion_uses_family_census_when_available(tmp_path, query_image_bytes):
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_family_test", "top", {"real": norm_vec})
    close_but_same_family = _unit([0.97, 0.05, 0.01])
    _upsert_slug(store, "fusion_family_test", "sibling", {"real": close_but_same_family})
    stranger = _unit([0.9, 0.1, 0.02])
    _upsert_slug(store, "fusion_family_test", "stranger", {"real": stranger})

    families_path = tmp_path / "families.json"
    families_path.write_text(json.dumps({"fam1": {"slugs": ["top", "sibling"]}}), encoding="utf-8")
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_family_test",
        manifest_path=tmp_path / "manifest.json", families_json=families_path,
    )
    matches = index.search_fusion(query_image_bytes, top_k=5)
    top = matches[0]
    assert top.slug == "top"
    stranger_match = next(m for m in matches if m.slug == "stranger")
    # gap обязан пропустить "sibling" (та же семья) и указать на "stranger",
    # даже если "sibling" скор-ближе к "top", чем "stranger".
    assert top.gap == pytest.approx(top.score - stranger_match.score)


# --------------------------------------------------------------------------------------
# _exact_scores_for_slugs(): точный (не ANN) добор конкретных slug'ов
# --------------------------------------------------------------------------------------


def test_exact_scores_for_slugs_matches_manual_cosine(tmp_path):
    q1, q2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    v_a, v_b = _unit([0.8, 0.2, 0.1]), _unit([0.1, 0.2, 0.9])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "exact_test", "a", {"real": v_a})
    _upsert_slug(store, "exact_test", "b", {"real": v_b})
    index = ImageIndex(store=store, encoder=_ShapeKeyedEncoder(q1, q2), collection="exact_test",
                        manifest_path=tmp_path / "manifest.json")

    out = index._exact_scores_for_slugs(["a", "b"], (q1, q2))
    assert out["a"][0] == pytest.approx(max(np.dot(v_a, q1), np.dot(v_a, q2)))
    assert out["b"][0] == pytest.approx(max(np.dot(v_b, q1), np.dot(v_b, q2)))
    assert out["a"][1] == "real" and out["b"][1] == "real"


def test_exact_scores_for_slugs_takes_max_across_a_slugs_own_multiple_views(tmp_path):
    """Слаг с несколькими точками (real + synth-N) — берётся максимум СРЕДИ ЕГО
    ЖЕ точек тоже, не только среди query-векторов (та же дисциплина, что
    `search()` уже применяет к обычному ANN-схлопыванию)."""
    q1, q2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    weak_view = _unit([0.1, 0.05, 0.99])  # почти ортогонален обоим query-векторам
    strong_view = _unit([0.9, 0.1, 0.05])  # хорошо совпадает с q1
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "exact_multi_test", "multi-view-slug", {"real": weak_view, "synth-1": strong_view})
    index = ImageIndex(store=store, encoder=_ShapeKeyedEncoder(q1, q2), collection="exact_multi_test",
                        manifest_path=tmp_path / "manifest.json")

    out = index._exact_scores_for_slugs(["multi-view-slug"], (q1, q2))
    score, view = out["multi-view-slug"]
    assert score == pytest.approx(max(np.dot(strong_view, q1), np.dot(strong_view, q2)), abs=1e-5)
    assert view == "synth-1"  # побеждающая точка — сильный ракурс, не первый по порядку


def test_exact_scores_for_slugs_empty_slug_list_returns_empty_dict(tmp_path):
    q1, q2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    index = ImageIndex(store=store, encoder=_ShapeKeyedEncoder(q1, q2), collection="whatever",
                        manifest_path=tmp_path / "manifest.json")
    assert index._exact_scores_for_slugs([], (q1, q2)) == {}


def test_exact_scores_for_slugs_missing_collection_returns_empty_dict(tmp_path):
    q1, q2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    index = ImageIndex(store=store, encoder=_ShapeKeyedEncoder(q1, q2), collection="never_created",
                        manifest_path=tmp_path / "manifest.json")
    assert index._exact_scores_for_slugs(["anything"], (q1, q2)) == {}


def test_exact_scores_for_slugs_ignores_slugs_not_requested(tmp_path):
    """Фильтр `MatchAny` обязан вернуть ТОЛЬКО запрошенные slug'и, даже когда в
    коллекции есть другие позиции с более высоким скором."""
    q1, q2 = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "exact_filter_test", "requested", {"real": _unit([0.5, 0.5, 0.1])})
    _upsert_slug(store, "exact_filter_test", "not-requested-but-closer", {"real": q1})
    index = ImageIndex(store=store, encoder=_ShapeKeyedEncoder(q1, q2), collection="exact_filter_test",
                        manifest_path=tmp_path / "manifest.json")
    out = index._exact_scores_for_slugs(["requested"], (q1, q2))
    assert set(out) == {"requested"}


def test_match_dataclass_unaffected_by_fusion_changes():
    """Регресс: `search_fusion` не должен были незаметно поменять форму `Match`
    (тот же контракт, что `test_match_is_plain_dataclass_per_contract` в
    test_index.py — дублируем короткую проверку здесь на всякий случай импорта)."""
    m = Match(slug="x", score=0.5, gap=0.1, view="real")
    assert (m.slug, m.score, m.gap, m.view) == ("x", 0.5, 0.1, "real")


def test_point_id_helper_still_importable():
    """Сверка, что `cv.store.point_id` (используется фикстурами этого файла
    косвенно через `store.upsert`) не сломан этой волной — быстрый канареечный
    тест импорта, не переизобретение test_store.py."""
    assert point_id("a::real") == point_id("a::real")
    assert point_id("a::real") != point_id("b::real")


# --------------------------------------------------------------------------------------
# agents/H2-rapidocr-multiscale.md п.4: CV_FUSION_CROPS=2|8 — embed_fusion_query()
# возвращает N векторов (2 дефолт, 8 = + три центральных кропа cwide/cmid/ctight ×
# как есть/normalize_query), search_fusion(vectors=...) принимает любой N.
# --------------------------------------------------------------------------------------


class _ShapeRecordingEncoder:
    """Помнит форму (H, W) КАЖДОГО массива, переданного `encode_batch()` за ОДИН
    вызов — проверяет, что `embed_fusion_query()` (а) кодирует одним батчем, не N
    отдельными вызовами `encode()`, и (б) строит именно те кропы (в том порядке),
    что заявлены `FUSION_EXTRA_CROPS`. Векторы-заглушки различимы по индексу, сама
    модель здесь не нужна."""

    def __init__(self):
        self.batch_calls: list[list[tuple[int, int]]] = []

    def encode(self, image: np.ndarray, use_cache: bool = True) -> list[float]:
        raise AssertionError("embed_fusion_query() обязан звать encode_batch(), не encode() поштучно")

    def encode_batch(self, images: list[np.ndarray], use_cache: bool = True) -> list[list[float]]:
        self.batch_calls.append([tuple(im.shape[:2]) for im in images])
        return [[float(i)] for i in range(len(images))]


def _index_with_spy(tmp_path, spy, **overrides) -> ImageIndex:
    store = QdrantStore(path=tmp_path / "qdrant")
    return ImageIndex(
        store=store, encoder=spy, collection="fusion_crops_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
        **overrides,
    )


def test_fusion_crops_default_is_2(tmp_path):
    index = _index_with_spy(tmp_path, _ShapeRecordingEncoder())
    assert index.fusion_crops == 2


def test_fusion_crops_constructor_param_overrides_default(tmp_path):
    index = _index_with_spy(tmp_path, _ShapeRecordingEncoder(), fusion_crops=8)
    assert index.fusion_crops == 8


def test_fusion_crops_env_var_overrides_constructor_default(tmp_path, monkeypatch):
    """Тот же принцип живого резолва в конструкторе, что `LabelVerifier.ocr_query_mode`
    (packages/cv/cv/verify.py) — env выставлен ДО конструктора, побеждает дефолт."""
    monkeypatch.setenv("CV_FUSION_CROPS", "8")
    index = _index_with_spy(tmp_path, _ShapeRecordingEncoder(), fusion_crops=2)
    assert index.fusion_crops == 8


def test_embed_fusion_query_default_two_crops_single_batch_call(tmp_path, query_image_bytes):
    spy = _ShapeRecordingEncoder()
    index = _index_with_spy(tmp_path, spy)

    vectors = index.embed_fusion_query(query_image_bytes)

    assert len(vectors) == 2
    assert len(spy.batch_calls) == 1, "ОДИН вызов encode_batch(), не N отдельных encode()"
    assert len(spy.batch_calls[0]) == 2


def test_embed_fusion_query_eight_crops_shapes_match_fusion_extra_crops_fractions(tmp_path, query_image_bytes):
    """Порядок и формы всех 8 массивов, реально дошедших до энкодера: [кроп
    детектора (канонический квадрат normalize_query), весь кадр как есть] + для
    КАЖДОГО из cwide/cmid/ctight (в порядке `FUSION_EXTRA_CROPS`) — [кроп как
    есть, тот же кроп через normalize_query]."""
    from cv.imageio import decode_image
    from cv.index import FUSION_EXTRA_CROPS

    spy = _ShapeRecordingEncoder()
    index = _index_with_spy(tmp_path, spy, fusion_crops=8)

    vectors = index.embed_fusion_query(query_image_bytes)

    assert len(vectors) == 8
    assert len(spy.batch_calls) == 1, "8 кропов — тоже ОДИН батч-вызов, не восемь"
    arr = decode_image(query_image_bytes)
    h, w = arr.shape[:2]
    expected = [(NORM_SIZE_DEFAULT, NORM_SIZE_DEFAULT), (h, w)]
    for x0, y0, x1, y1 in FUSION_EXTRA_CROPS.values():
        expected.append((int(h * y1) - int(h * y0), int(w * x1) - int(w * x0)))  # "как есть"
        expected.append((NORM_SIZE_DEFAULT, NORM_SIZE_DEFAULT))  # через normalize_query
    assert spy.batch_calls[0] == expected


def test_search_fusion_eight_crops_still_finds_per_slug_max_across_all_vectors(tmp_path, query_image_bytes):
    """CV_FUSION_CROPS=8: `search_fusion()` обязан работать по ВСЕМ 8 векторам, не
    только по первым двум (регресс на распаковку `norm_vec, raw_vec = vectors`,
    которая жёстко требовала ровно 2 элемента ДО этой правки) — тот же принцип
    per-slug максимума, что `test_search_fusion_max_combines_norm_and_raw_views`."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "norm-favorite", {"real": norm_vec})
    _upsert_slug(store, "fusion_test", "raw-favorite", {"real": raw_vec})

    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
        fusion_crops=8,
    )
    vectors = index.embed_fusion_query(query_image_bytes)
    assert len(vectors) == 8

    matches = index.search_fusion(None, top_k=5, vectors=vectors)
    by_slug = {m.slug: m.score for m in matches}
    assert by_slug["norm-favorite"] == pytest.approx(1.0)
    assert by_slug["raw-favorite"] == pytest.approx(1.0)


def test_search_fusion_eight_crops_extra_slug_recovery_still_works(tmp_path, query_image_bytes):
    """`_exact_scores_for_slugs()` (extra_slugs-добор) тоже обязан принять
    8-элементный `vectors` — та же логика "слаг вне ANN-топа находится точным
    скором", что `test_search_fusion_extra_slug_missing_from_collection_is_silently_absent`,
    только числом векторов 8, не 2."""
    norm_vec, raw_vec = _unit([1, 0, 0]), _unit([0, 1, 0])
    store = QdrantStore(path=tmp_path / "qdrant")
    _upsert_slug(store, "fusion_test", "only-slug", {"real": norm_vec})
    index = ImageIndex(
        store=store, encoder=_ShapeKeyedEncoder(norm_vec, raw_vec), collection="fusion_test",
        manifest_path=tmp_path / "manifest.json", families_json=tmp_path / "no-families.json",
        fusion_crops=8,
    )
    matches = index.search_fusion(query_image_bytes, top_k=5, extra_slugs=["never-indexed-slug"])
    assert "never-indexed-slug" not in {m.slug for m in matches}
    assert {m.slug for m in matches} == {"only-slug"}
