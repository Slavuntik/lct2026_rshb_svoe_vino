"""Тесты `ml-handoff/scripts/make_triplets.py` — на фикстурах, НЕ на `case-data/`
(бриф agents/M1-ml-handoff.md: "тест положи в ml-handoff/tests/", в `qa/tests/` не лезть).

Запуск (строго venv packages/cv — нужны numpy/cv2 из cv.augment для одного теста,
остальные — чистые юниты, но общий модуль их всё равно импортирует):

    cd svoy-somelye
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 packages/cv/.venv/bin/python -m pytest \\
        -q ml-handoff/tests/test_make_triplets.py

Фикстуры — синтетические (numpy/JPEG на лету, как `packages/cv/tests/conftest.py::
synthetic_bottle_image`), не реальные фото кейса — ничего от `case-data/` не читаем.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make_triplets.py"
_spec = importlib.util.spec_from_file_location("make_triplets", _SCRIPT_PATH)
make_triplets = importlib.util.module_from_spec(_spec)
sys.modules["make_triplets"] = make_triplets
_spec.loader.exec_module(make_triplets)  # type: ignore[union-attr]


# --------------------------------------------------------------------------------------
# Фикстуры: маленький синтетический "case-data" каталог (НЕ настоящие данные кейса)
# --------------------------------------------------------------------------------------


def _tiny_bottle_image(seed: int) -> np.ndarray:
    """Тот же приём, что packages/cv/tests/conftest.py::synthetic_bottle_image —
    маленькая детерминированная "этикетка", без сети/диска на вход."""
    rng = np.random.default_rng(seed)
    img = np.full((120, 80, 3), 40, dtype=np.uint8)
    img[60:100, 10:70] = rng.integers(80, 220, size=(40, 60, 3), dtype=np.uint8)
    return img


# Семья near-dup: wine-a и wine-c (одна линейка, "прочее"-различитель).
# wine-b/wine-f/wine-g/wine-h/wine-i — вне всякой семьи (пул для random-негативов).
# wine-d — usable=false (исключается). wine-e — usable=true, но файла на диске нет
# (имитирует no_ref_slugs / физически отсутствующий файл поставки).
_SLUGS_WITH_FILES = ["wine-a", "wine-b", "wine-c", "wine-f", "wine-g", "wine-h", "wine-i"]


def _slug_refs_payload() -> dict:
    mapping = {
        "wine-a": {
            "chosen": "wine_a.jpg",
            "candidates": ["wine_a.jpg", "wine_a_alt.jpg"],
            "usable": True,
            "winery": "Winery A",
            "name": "Alpha",
            "ref_quality_class": "label_closeup",
            "match_method": "normalized_stem",
        },
        "wine-b": {
            "chosen": "wine_b.jpg",
            "candidates": ["wine_b.jpg"],
            "usable": True,
            "winery": "Winery B",
            "name": "Beta",
            "ref_quality_class": "bottle_in_scene",
            "match_method": "normalized_stem",
        },
        "wine-c": {
            "chosen": "wine_c.jpg",
            "candidates": ["wine_c.jpg", "wine_c_alt1.jpg", "wine_c_alt2.jpg", "wine_c_alt3.jpg"],
            "usable": True,
            "winery": "Winery A",
            "name": "Gamma",
            "ref_quality_class": "label_closeup",
            "match_method": "normalized_stem",
        },
        "wine-d": {
            "chosen": "wine_d.jpg",
            "candidates": ["wine_d.jpg"],
            "usable": False,  # триаж SigLIP2: шумный эталон — должен быть исключён
            "winery": "Winery D",
            "name": "Delta",
            "match_method": "normalized_stem",
        },
        "wine-e": {
            "chosen": "missing.jpg",  # файла нет на диске — имитация no_ref
            "candidates": ["missing.jpg"],
            "usable": True,
            "winery": "Winery E",
            "name": "Epsilon",
            "match_method": "normalized_stem",
        },
        "wine-f": {
            "chosen": "wine_f.jpg",
            "candidates": ["wine_f.jpg"],
            "usable": True,
            "winery": "Winery F",
            "name": "Zeta",
            "ref_quality_class": "bottle_in_scene",
            "match_method": "cyrillic_transliteration",
        },
        "wine-g": {
            "chosen": "wine_g.jpg",
            "candidates": ["wine_g.jpg"],
            "usable": True,
            "winery": "Winery G",
            "name": "Eta",
            "match_method": "normalized_stem",
        },
        "wine-h": {
            "chosen": "wine_h.jpg",
            "candidates": ["wine_h.jpg"],
            "usable": True,
            "winery": "Winery H",
            "name": "Theta",
            "match_method": "normalized_stem",
        },
        "wine-i": {
            "chosen": "wine_i.jpg",
            "candidates": ["wine_i.jpg"],
            "usable": True,
            "winery": "Winery I",
            "name": "Iota",
            "match_method": "normalized_stem",
        },
    }
    return {"generated_by": "test fixture, not qa/case_census.py", "mapping": mapping}


def _families_payload() -> dict:
    return {
        "alpha-gamma-family": {
            "slugs": ["wine-a", "wine-c"],
            "chosen_files": {"wine-a": "wine_a.jpg", "wine-c": "wine_c.jpg"},
            "differentiator": "прочее",
        }
    }


@pytest.fixture()
def case_data_dir(tmp_path: Path) -> Path:
    case_dir = tmp_path / "case-data"
    uploads_dir = case_dir / make_triplets.DEFAULT_UPLOADS_SUBPATH
    uploads_dir.mkdir(parents=True)

    (case_dir / "slug_refs.json").write_text(json.dumps(_slug_refs_payload(), ensure_ascii=False), encoding="utf-8")
    (case_dir / "families.json").write_text(json.dumps(_families_payload(), ensure_ascii=False), encoding="utf-8")

    from cv import imageio

    filenames = [
        "wine_a.jpg",
        "wine_a_alt.jpg",
        "wine_b.jpg",
        "wine_c.jpg",
        "wine_c_alt1.jpg",
        "wine_c_alt2.jpg",
        "wine_c_alt3.jpg",
        "wine_f.jpg",
        "wine_g.jpg",
        "wine_h.jpg",
        "wine_i.jpg",
    ]
    for i, fname in enumerate(filenames):
        img = _tiny_bottle_image(seed=i)
        (uploads_dir / fname).write_bytes(imageio.encode_jpeg(img))
    # wine_d.jpg и missing.jpg намеренно НЕ создаём.
    return case_dir


@pytest.fixture()
def uploads_dir(case_data_dir: Path) -> Path:
    return case_data_dir / make_triplets.DEFAULT_UPLOADS_SUBPATH


# --------------------------------------------------------------------------------------
# 1. Ключевое свойство рецепта позитивов: view_index воспроизводим НЕЗАВИСИМО от n_views
# --------------------------------------------------------------------------------------


def test_view_recipe_is_prefix_consistent_with_augment():
    """Обоснование дизайна манифеста (докстринг make_triplets.py): "seed + view_index"
    без n_views, равного n на сборке, всё равно детерминированно воспроизводит ТОТ ЖЕ
    кадр — потому что `render_synthetic_views` тянет параметры ракурсов из ОДНОГО
    Generator(seed) строго по порядку. Проверяем на реальной `cv.augment`, не на своей
    копии логики."""
    from cv.augment import render_synthetic_views

    image = _tiny_bottle_image(seed=0)
    seed = make_triplets.DEFAULT_TRIPLET_SEED

    full = render_synthetic_views(image, n=24, seed=seed)
    small = render_synthetic_views(image, n=3, seed=seed)

    for i in range(3):
        assert np.array_equal(full[i], small[i]), f"view {i} разошёлся между n=24 и n=3"

    # И повторный вызов с теми же параметрами даёт то же самое (сам augment уже
    # тестирует детерминизм в packages/cv/tests/test_augment.py — здесь только
    # подтверждаем, что НАШ манифест ссылается ровно на этот же контракт).
    again = render_synthetic_views(image, n=3, seed=seed)
    assert np.array_equal(small[1], again[1])


# --------------------------------------------------------------------------------------
# 2. Загрузка каталога: usable-фильтр, отсутствующие файлы, доп. реальные ракурсы
# --------------------------------------------------------------------------------------


def test_load_anchors_filters_unusable_and_missing_files(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)

    assert set(anchors) == set(_SLUGS_WITH_FILES)  # wine-d (unusable) и wine-e (нет файла) — вне
    assert "wine-d" not in anchors
    assert "wine-e" not in anchors

    assert anchors["wine-a"].path.name == "wine_a.jpg"
    assert [p.name for p in anchors["wine-a"].extra_real] == ["wine_a_alt.jpg"]
    assert anchors["wine-a"].winery == "Winery A"
    assert anchors["wine-a"].multi_candidate_count == 2

    # wine-c: 4 кандидата -> порог осторожности (f3-case-census.md §1.2, "≥4 кандидата")
    assert anchors["wine-c"].multi_candidate_count == 4


# --------------------------------------------------------------------------------------
# 3. Негативы по семье
# --------------------------------------------------------------------------------------


def test_family_negatives_exclude_self_and_respect_cap(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)
    family_by_slug, members_by_slug = make_triplets.load_family_index(case_data_dir / "families.json")

    assert family_by_slug["wine-a"] == "alpha-gamma-family"
    assert family_by_slug["wine-c"] == "alpha-gamma-family"
    assert "wine-b" not in family_by_slug

    negs = make_triplets.build_family_negatives(
        "wine-a", family_by_slug["wine-a"], members_by_slug["wine-a"], anchors, cap=7
    )
    assert [n.slug for n in negs] == ["wine-c"]
    assert negs[0].kind == "family_hard"
    assert negs[0].family_id == "alpha-gamma-family"

    capped = make_triplets.build_family_negatives(
        "wine-a", family_by_slug["wine-a"], members_by_slug["wine-a"], anchors, cap=0
    )
    assert capped == []


def test_family_negatives_empty_for_slug_without_family(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)
    family_by_slug, members_by_slug = make_triplets.load_family_index(case_data_dir / "families.json")

    assert make_triplets.build_family_negatives(
        "wine-b", family_by_slug.get("wine-b"), members_by_slug.get("wine-b", frozenset()), anchors, cap=7
    ) == []


# --------------------------------------------------------------------------------------
# 4. Случайные негативы: детерминизм, исключение семьи, чувствительность к seed
# --------------------------------------------------------------------------------------


def test_random_negatives_deterministic_and_excludes_family(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)
    _, members_by_slug = make_triplets.load_family_index(case_data_dir / "families.json")
    all_slugs = sorted(anchors)

    run1 = make_triplets.build_random_negatives("wine-a", members_by_slug.get("wine-a", frozenset()), anchors, all_slugs, cap=3, seed=555001)
    run2 = make_triplets.build_random_negatives("wine-a", members_by_slug.get("wine-a", frozenset()), anchors, all_slugs, cap=3, seed=555001)

    assert [n.slug for n in run1] == [n.slug for n in run2]  # детерминизм по (seed, slug)
    assert len(run1) == 3
    assert "wine-a" not in {n.slug for n in run1}
    assert "wine-c" not in {n.slug for n in run1}  # член семьи wine-a — не рандомный негатив
    assert all(n.kind == "random" for n in run1)


def test_random_negatives_differ_with_different_seed(case_data_dir: Path, uploads_dir: Path):
    """Пул кандидатов у фикстуры маленький (5 слагов вне семьи wine-a), поэтому
    сравнивать РОВНО два произвольных сида ненадёжно — C(5,3)=10 подмножеств, у двух
    независимых сидов заметный шанс случайно совпасть (и однажды совпало: сиды 1/2).
    Сравниваем через 6 сидов сразу — чтобы ВСЕ шесть дали одно и то же подмножество
    случайно, шанс пренебрежимо мал; тест проверяет "seed реально влияет", а не
    конкретную пару чисел."""
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)
    _, members_by_slug = make_triplets.load_family_index(case_data_dir / "families.json")
    all_slugs = sorted(anchors)
    family = members_by_slug.get("wine-a", frozenset())

    results = [
        tuple(n.slug for n in make_triplets.build_random_negatives("wine-a", family, anchors, all_slugs, cap=3, seed=s))
        for s in range(6)
    ]
    assert len(set(results)) > 1, f"6 разных сидов дали одно и то же подмножество: {results[0]}"


# --------------------------------------------------------------------------------------
# 5. Позитивы: синтетические спецификации + real_alternate с caution-флагом
# --------------------------------------------------------------------------------------


def test_positive_specs_synthetic_and_real_alternate(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)

    pos_a = make_triplets.build_positive_specs(anchors["wine-a"], n_positives=4, n_views=24, seed=555001)
    synth = [p for p in pos_a if p.kind == "synthetic_view"]
    real = [p for p in pos_a if p.kind == "real_alternate"]
    assert [p.view_index for p in synth] == [0, 1, 2, 3]
    assert all(p.seed == 555001 and p.n_views == 24 for p in synth)
    assert len(real) == 1 and real[0].caution is False  # 2 кандидата < порога 4

    pos_c = make_triplets.build_positive_specs(anchors["wine-c"], n_positives=2, n_views=24, seed=555001)
    real_c = [p for p in pos_c if p.kind == "real_alternate"]
    assert len(real_c) == 3
    assert all(p.caution is True for p in real_c)  # 4 кандидата >= порога — f3-case-census.md §1.2

    pos_b = make_triplets.build_positive_specs(anchors["wine-b"], n_positives=4, n_views=24, seed=555001)
    assert all(p.kind == "synthetic_view" for p in pos_b)  # без доп. кандидатов


def test_positive_specs_cap_by_n_views():
    from make_triplets import Anchor

    anchor = Anchor("x", "W", "N", None, Path("/x.jpg"), [], 0)
    pos = make_triplets.build_positive_specs(anchor, n_positives=10, n_views=3, seed=1)
    assert len(pos) == 3  # позитивов не может быть больше, чем всего ракурсов


# --------------------------------------------------------------------------------------
# 6. Соседи по эмбеддингу — чистая арифметика, БЕЗ модели/энкодера
# --------------------------------------------------------------------------------------


def test_neighbor_negatives_ranks_by_cosine_similarity(case_data_dir: Path, uploads_dir: Path):
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)
    embeddings = {
        "wine-a": [1.0, 0.0],
        "wine-b": [0.99, 0.01],   # почти совпадает по направлению — ближайший сосед
        "wine-f": [0.0, 1.0],     # ортогонален — далёкий
        "wine-g": [0.7, 0.3],
    }
    negs = make_triplets.build_neighbor_negatives("wine-a", embeddings, exclude={"wine-a"}, cap=2, anchors=anchors)
    assert [n.slug for n in negs] == ["wine-b", "wine-g"]
    assert negs[0].kind == "index_neighbor"
    assert negs[0].similarity > negs[1].similarity


def test_neighbor_negatives_empty_without_cache_or_cap():
    assert make_triplets.build_neighbor_negatives("wine-a", {}, set(), cap=2, anchors={}) == []


# --------------------------------------------------------------------------------------
# 7. Сборка манифеста целиком: согласованность счётчиков (deliverable-метрика M1)
# --------------------------------------------------------------------------------------


def test_build_manifest_end_to_end_counts_and_schema(case_data_dir: Path, uploads_dir: Path):
    groups, summary = make_triplets.build_manifest(
        case_data_dir,
        uploads_dir,
        positives_per_anchor=4,
        n_views=24,
        seed=555001,
        neg_family_cap=7,
        neg_random_cap=3,
        neg_neighbor_cap=0,
        embeddings=None,
        limit=None,
    )

    assert summary.anchors_total == len(_SLUGS_WITH_FILES) == 7
    assert summary.anchors_with_family_negatives == 2  # wine-a, wine-c
    assert summary.distinct_families_used == 1  # alpha-gamma-family
    assert summary.negatives_family_total == 2  # wine-a->wine-c, wine-c->wine-a

    # Арифметика сводки должна буквально сойтись с самими группами (не разъехаться).
    assert summary.positives_total == sum(len(g.positives) for g in groups)
    assert summary.negatives_total == sum(len(g.negatives) for g in groups)
    assert summary.triplets_expanded_total == sum(len(g.positives) * len(g.negatives) for g in groups)
    assert len(groups) == summary.anchors_total  # у каждого якоря нашлись негативы (random подстраховал)

    # Каждая группа сериализуется в валидный JSON с ожидаемыми ключами.
    for g in groups:
        payload = g.to_json()
        json.dumps(payload, ensure_ascii=False)  # не бросает
        assert set(payload) == {"slug", "winery", "name", "family_id", "ref_quality_class", "anchor", "positives", "negatives"}
        assert payload["anchor"]["path"]
        for pos in payload["positives"]:
            assert pos["kind"] in ("synthetic_view", "real_alternate")
        for neg in payload["negatives"]:
            assert neg["kind"] in ("family_hard", "random", "index_neighbor")
            assert neg["slug"] != payload["slug"]  # негатив никогда не сам якорь


# --------------------------------------------------------------------------------------
# 8. CLI (run()) целиком: файлы на диске, коды возврата
# --------------------------------------------------------------------------------------


def test_run_writes_valid_jsonl_and_summary(case_data_dir: Path, tmp_path: Path):
    out_path = tmp_path / "triplets.jsonl"
    code = make_triplets.run(["--case-data-dir", str(case_data_dir), "--out", str(out_path), "--neg-random-cap", "2"])
    assert code == 0
    assert out_path.is_file()

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7  # anchors_total фикстуры
    records = [json.loads(line) for line in lines]
    assert {r["slug"] for r in records} == set(_SLUGS_WITH_FILES)

    summary_path = out_path.with_suffix(out_path.suffix + ".summary.json")
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["anchors_total"] == 7
    assert summary["distinct_families_used"] == 1
    assert summary["triplets_expanded_total"] == sum(len(r["positives"]) * len(r["negatives"]) for r in records)


def test_run_errors_when_case_data_dir_missing(tmp_path: Path):
    code = make_triplets.run(["--case-data-dir", str(tmp_path / "nope"), "--out", str(tmp_path / "out.jsonl")])
    assert code == 2


def test_run_errors_without_out_or_build_cache(case_data_dir: Path):
    code = make_triplets.run(["--case-data-dir", str(case_data_dir)])
    assert code == 2


def test_run_limit_caps_number_of_anchors(case_data_dir: Path, tmp_path: Path):
    out_path = tmp_path / "triplets.jsonl"
    code = make_triplets.run(["--case-data-dir", str(case_data_dir), "--out", str(out_path), "--limit", "2"])
    assert code == 0
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_run_single_anchor_catalog_is_usage_error(tmp_path: Path):
    """Каталог без пула для random-негативов и без семьи — вырожденный случай
    (1 usable-якорь, негативов взять неоткуда) — та же конвенция, что
    `qa/scan_eval.py` ("пустой eval-сет после фильтра" -> код 2), не тихий 0-триплетов."""
    case_dir = tmp_path / "case-data"
    uploads_dir = case_dir / make_triplets.DEFAULT_UPLOADS_SUBPATH
    uploads_dir.mkdir(parents=True)
    payload = {"mapping": {"solo": {"chosen": "solo.jpg", "candidates": ["solo.jpg"], "usable": True, "winery": "W", "name": "N"}}}
    (case_dir / "slug_refs.json").write_text(json.dumps(payload), encoding="utf-8")
    (case_dir / "families.json").write_text(json.dumps({}), encoding="utf-8")

    from cv import imageio

    (uploads_dir / "solo.jpg").write_bytes(imageio.encode_jpeg(_tiny_bottle_image(0)))

    code = make_triplets.run(["--case-data-dir", str(case_dir), "--out", str(tmp_path / "out.jsonl")])
    assert code == 2


# --------------------------------------------------------------------------------------
# 9. Эмбеддинг-кэш: только логика вызова, без реальной модели (SiglipEncoder дорогой)
# --------------------------------------------------------------------------------------


def test_build_embeddings_cache_uses_injected_encoder(case_data_dir: Path, uploads_dir: Path, monkeypatch):
    """НЕ грузит настоящий SigLIP2 (дорого/требует тёплый HF-кэш) — подменяем
    `cv.encoder.SiglipEncoder` фейком через monkeypatch модуля `cv.encoder`, куда
    `build_embeddings_cache` ходит лениво (`from cv.encoder import SiglipEncoder`
    внутри функции) — проверяем именно ЧТО функция кладёт в кэш, не сам энкодер
    (у него свои тесты в packages/cv/tests/test_encoder.py)."""
    anchors = make_triplets.load_anchors(case_data_dir, uploads_dir)

    class _FakeEncoder:
        def __init__(self):
            self.calls = 0

        def encode(self, image, use_cache=True):
            self.calls += 1
            return [float(image.mean()), float(self.calls)]

    import cv.encoder as cv_encoder_module

    monkeypatch.setattr(cv_encoder_module, "SiglipEncoder", _FakeEncoder)

    out_path = uploads_dir.parent / "embeddings.json"
    cache = make_triplets.build_embeddings_cache(anchors, out_path, limit=3)

    assert len(cache) == 3  # limit=3
    assert out_path.is_file()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk == cache
    assert all(len(v) == 2 for v in cache.values())
