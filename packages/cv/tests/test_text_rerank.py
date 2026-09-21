"""Тесты cv/text_rerank.py — транслитерация, IDF-веса, опечатки OCR, пустой OCR
(agents/G5-accuracy.md, задача 6). Чистые юниты, без энкодера/OCR/Qdrant — быстрые
(тот же принцип, что test_families.py: `text_rerank.py` не тянет torch/cv2)."""
from __future__ import annotations

import csv

from cv.text_rerank import (
    CatalogText,
    FUZZY_WEIGHT,
    build_idf,
    candidate_text,
    default_catalog_csv_path,
    distinctive_idf_threshold,
    has_distinctive_token,
    load_catalog_text,
    normalize_text,
    rerank_top_k,
    text_score,
    tokenize,
    translit_cyr_to_lat,
)


# --------------------------------------------------------------------------------------
# Транслитерация / нормализация
# --------------------------------------------------------------------------------------


def test_translit_cyr_to_lat_matches_catalog_slug_convention():
    """Схема совпадает с той, что уже видна в реальных slug'ах каталога кейса
    (докстринг модуля): "мускатель"->"muskatel", "белый"->"belyy"."""
    assert translit_cyr_to_lat("мускатель") == "muskatel"
    assert translit_cyr_to_lat("белый") == "belyy"


def test_normalize_text_lowercases_yo_and_strips_punctuation():
    assert normalize_text("Ёлки, ВИНО!!!") == normalize_text("елки вино")


def test_normalize_text_empty_and_whitespace_only():
    assert normalize_text("") == ""
    assert normalize_text("   \t  ") == ""


def test_normalize_text_transliterates_cyrillic_to_latin():
    assert normalize_text("Аристов") == "aristov"


def test_normalize_text_leaves_latin_as_is_lowercased():
    assert normalize_text("ARISTOV") == "aristov"


def test_normalize_text_both_sides_meet_in_latin_space():
    """"с обеих сторон" (докстринг модуля): этикетка-латиница и каталог-кириллица
    нормализуются в ОДНО и то же значение, а не просто в "какой-то" канон."""
    label = normalize_text("ARISTOV DONUM")
    catalog_name = normalize_text("Аристов Донум")
    assert label == catalog_name == "aristov donum"


def test_tokenize_splits_on_whitespace_after_normalization():
    assert tokenize("Шато Тамань, Мерло!") == ["shato", "taman", "merlo"]


# --------------------------------------------------------------------------------------
# Каталог из CSV: первое непустое значение на слаг (колонки дублируются)
# --------------------------------------------------------------------------------------


def _write_catalog_csv(tmp_path, rows: list[dict[str, str]]):
    path = tmp_path / "catalog.csv"
    fieldnames = ["Название вина", "Категория", "Цвет", "Регион", "Сорт винограда", "Описание", "Винодельня", "Slug", "Название фото"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def test_load_catalog_text_dedups_slug_takes_first_row(tmp_path):
    path = _write_catalog_csv(tmp_path, [
        {"Название вина": "Мерло", "Регион": "Кубань", "Сорт винограда": "Мерло", "Винодельня": "Шато Тамань", "Slug": "merlo-slug"},
        {"Название вина": "Мерло", "Регион": "Кубань", "Сорт винограда": "Мерло", "Винодельня": "Шато Тамань", "Slug": "merlo-slug"},
    ])
    catalog = load_catalog_text(path)
    assert set(catalog) == {"merlo-slug"}
    assert catalog["merlo-slug"].winery == "Шато Тамань"


def test_load_catalog_text_fills_blank_field_from_later_duplicate_row(tmp_path):
    """Первая строка слага несёт пустой Регион — вторая (тот же slug) его восполняет;
    "первое непустое" — по КАЖДОМУ полю независимо, не по строке целиком."""
    path = _write_catalog_csv(tmp_path, [
        {"Название вина": "Мерло", "Регион": "", "Сорт винограда": "Мерло", "Винодельня": "Шато Тамань", "Slug": "merlo-slug"},
        {"Название вина": "Мерло", "Регион": "Кубань", "Сорт винограда": "Мерло", "Винодельня": "Шато Тамань", "Slug": "merlo-slug"},
    ])
    catalog = load_catalog_text(path)
    assert catalog["merlo-slug"].region == "Кубань"


def test_load_catalog_text_skips_rows_without_slug(tmp_path):
    path = _write_catalog_csv(tmp_path, [{"Название вина": "Мерло", "Slug": ""}])
    assert load_catalog_text(path) == {}


def test_default_catalog_csv_path_uses_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.csv"
    monkeypatch.setenv("CV_CASE_CATALOG_CSV", str(custom))
    assert default_catalog_csv_path() == custom


def test_default_catalog_csv_path_falls_back_to_case_data_dir(monkeypatch, tmp_path):
    from cv import config
    monkeypatch.delenv("CV_CASE_CATALOG_CSV", raising=False)
    monkeypatch.setattr(config, "CASE_DATA_DIR", tmp_path)
    assert default_catalog_csv_path() == tmp_path / "strapi_output0709.csv"


def test_candidate_text_joins_non_empty_fields_only():
    entry = CatalogText(slug="s", name="Мерло", winery="", grape="Мерло", region="")
    assert candidate_text(entry) == "Мерло Мерло"


# --------------------------------------------------------------------------------------
# IDF: редкие токены весят больше частых
# --------------------------------------------------------------------------------------


def _catalog(entries: dict[str, tuple[str, str]]) -> dict[str, CatalogText]:
    """entries: slug -> (name, winery)."""
    return {slug: CatalogText(slug=slug, name=name, winery=winery) for slug, (name, winery) in entries.items()}


def test_build_idf_rare_token_weighted_above_common_token():
    catalog = _catalog({
        "a": ("Красное вино", "Шато Тамань"),
        "b": ("Белое вино", "Шато Тамань"),
        "c": ("Красное вино", "Абрау-Дюрсо"),
        "d": ("Красное вино", "Инкерман"),
    })
    idf = build_idf(catalog)
    # "вино"/"красное" — почти в каждом документе (частые); "шато"/"тамань" — в половине,
    # "инкерман"/"абрау"/"дюрсо" — в одном каждый (самые редкие).
    assert idf[tokenize("инкерман")[0]] > idf[tokenize("шато")[0]] > idf[tokenize("вино")[0]]


def test_build_idf_smooth_never_zero_even_for_universal_token():
    catalog = _catalog({"a": ("Вино", "X"), "b": ("Вино", "Y")})
    idf = build_idf(catalog)
    assert idf["vino"] > 0.0


# --------------------------------------------------------------------------------------
# text_score: транслитерация, опечатки OCR, пустой OCR, неизвестный кандидат
# --------------------------------------------------------------------------------------


def _idf_for(*catalogs: dict[str, CatalogText]) -> dict[str, float]:
    merged: dict[str, CatalogText] = {}
    for c in catalogs:
        merged.update(c)
    return build_idf(merged)


def test_text_score_empty_ocr_returns_zero():
    catalog = _catalog({"a": ("Мерло", "Шато Тамань")})
    idf = _idf_for(catalog)
    assert text_score("", catalog["a"], idf) == 0.0
    assert text_score("   ", catalog["a"], idf) == 0.0


def test_text_score_unknown_candidate_returns_zero():
    idf = _idf_for(_catalog({"a": ("Мерло", "Шато Тамань")}))
    assert text_score("Мерло Шато Тамань", None, idf) == 0.0


def test_text_score_translit_label_matches_cyrillic_catalog():
    """Ровно пример брифа: этикетка «ARISTOV» <-> каталог «Аристов»."""
    catalog = _catalog({
        "aristov-donum": ("Донум", "Аристов"),
        "unrelated": ("Шардоне", "Инкерман"),
    })
    idf = _idf_for(catalog)
    score_match = text_score("ARISTOV DONUM BRUT 2023", catalog["aristov-donum"], idf)
    score_other = text_score("ARISTOV DONUM BRUT 2023", catalog["unrelated"], idf)
    assert score_match > score_other
    assert score_match > 0.5  # оба различающих токена (aristov, donum) нашлись


def test_text_score_ocr_typo_still_scores_above_unrelated():
    """Опечатка OCR (пропущена буква: "МАСAНДРА" вместо "МАССАНДРА") — точное токенное
    совпадение рвётся, но rapidfuzz должен вытащить кандидата выше несвязанного."""
    catalog = _catalog({
        "massandra": ("Мускатель Белый", "Массандра"),
        "unrelated": ("Совиньон Блан", "Кубань-Вино"),
    })
    idf = _idf_for(catalog)
    ocr = "МУСКАТЕЛЬ МАСAНДРА БЕЛЫЙ"  # опечатка: МАССАНДРА -> МАСAНДРА (пропущена "С", лат. "A")
    score_match = text_score(ocr, catalog["massandra"], idf)
    score_other = text_score(ocr, catalog["unrelated"], idf)
    assert score_match > score_other


def test_text_score_exact_match_higher_than_partial():
    catalog = _catalog({
        "full": ("Мускатель Белый", "Массандра"),
        "partial": ("Мускатель Белый", "Инкерман"),
    })
    idf = _idf_for(catalog)
    ocr = "Мускатель Массандра Белый"
    assert text_score(ocr, catalog["full"], idf) > text_score(ocr, catalog["partial"], idf)


def test_fuzzy_weight_is_module_constant_between_zero_and_one():
    assert 0.0 < FUZZY_WEIGHT < 1.0


# --------------------------------------------------------------------------------------
# rerank_top_k: переранжирование top-K, пустой OCR -> порядок CV, хвост не трогается
# --------------------------------------------------------------------------------------


def test_rerank_top_k_empty_ocr_preserves_cv_order():
    catalog = _catalog({
        "a": ("Мерло", "Шато Тамань"),
        "b": ("Каберне", "Абрау-Дюрсо"),
        "c": ("Шардоне", "Инкерман"),
    })
    idf = _idf_for(catalog)
    ranked = [("a", 0.90), ("b", 0.85), ("c", 0.80)]
    result = rerank_top_k(ranked, "", catalog, idf, k=3, w=1.0)
    assert [slug for slug, _ in result] == ["a", "b", "c"]


def test_rerank_top_k_zero_weight_preserves_cv_order_even_with_text_signal():
    catalog = _catalog({
        "a": ("Мерло", "Шато Тамань"),
        "b": ("Каберне", "Абрау-Дюрсо"),
    })
    idf = _idf_for(catalog)
    ranked = [("a", 0.90), ("b", 0.85)]
    result = rerank_top_k(ranked, "Абрау-Дюрсо Каберне", catalog, idf, k=2, w=0.0)
    assert [slug for slug, _ in result] == ["a", "b"]


def test_rerank_top_k_strong_text_signal_promotes_lower_cv_candidate():
    """CV чуть предпочитает "a", но OCR однозначно указывает на "b" (near-miss
    ранжирования — ровно сценарий брифа: верный ответ в top-K, но не первый)."""
    catalog = _catalog({
        "a": ("Шардоне", "Инкерман"),
        "b": ("Мускатель Белый", "Массандра"),
    })
    idf = _idf_for(catalog)
    ranked = [("a", 0.90), ("b", 0.89)]
    result = rerank_top_k(ranked, "Мускатель Массандра Белый", catalog, idf, k=2, w=1.0)
    assert [slug for slug, _ in result][0] == "b"


def test_rerank_top_k_only_affects_head_not_tail():
    catalog = _catalog({
        "a": ("Шардоне", "Инкерман"),
        "b": ("Мерло", "Кубань-Вино"),
        "c": ("Мускатель Белый", "Массандра"),  # сильный текстовый сигнал, но вне top-K=2
    })
    idf = _idf_for(catalog)
    ranked = [("a", 0.90), ("b", 0.85), ("c", 0.80)]
    result = rerank_top_k(ranked, "Мускатель Массандра Белый", catalog, idf, k=2, w=1.0)
    # хвост (c) остаётся третьим и с исходным cv_score, несмотря на сильный текстовый сигнал —
    # переранжирование действует ТОЛЬКО внутри top-K, как задано брифом.
    assert result[2] == ("c", 0.80)


def test_rerank_top_k_preserves_length():
    catalog = _catalog({"a": ("X", "Y"), "b": ("Z", "W"), "c": ("Q", "R")})
    idf = _idf_for(catalog)
    ranked = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    result = rerank_top_k(ranked, "что-то", catalog, idf, k=2, w=1.0)
    assert len(result) == 3
    assert {slug for slug, _ in result} == {"a", "b", "c"}


# --------------------------------------------------------------------------------------
# Безопасный гейт min_token_idf (находка G5, 21.09): непустой, но БЕССОДЕРЖАТЕЛЬНЫЙ
# OCR-текст (только общая лексика каталога, либо мусор, которого в каталоге нет вовсе)
# должен вести себя КАК пустой — иначе на большом объёме такой "почти-пустой" сигнал
# статистически вредит чаще, чем помогает (full-dev свип реально регрессировал уже на
# w=0.01 без этого гейта — reports/g5-accuracy.md).
# --------------------------------------------------------------------------------------


def _catalog_for_gate() -> dict[str, CatalogText]:
    # "вино"/"красное"/"сухое" — в КАЖДОМ документе (низкий IDF, стоп-слово-подобные);
    # "инкерман"/"массандра"/"мускатель" — редкие, по одному документу (высокий IDF).
    return _catalog({
        "a": ("Красное сухое вино", "Инкерман"),
        "b": ("Красное сухое вино", "Массандра"),
        "c": ("Красное сухое Мускатель вино", "Кубань-Вино"),
    })


def test_distinctive_idf_threshold_is_the_median():
    idf = _idf_for(_catalog_for_gate())
    values = sorted(idf.values())
    assert distinctive_idf_threshold(idf) == values[len(values) // 2]


def test_has_distinctive_token_false_for_empty():
    idf = _idf_for(_catalog_for_gate())
    threshold = distinctive_idf_threshold(idf)
    assert has_distinctive_token("", idf, threshold) is False
    assert has_distinctive_token("   ", idf, threshold) is False


def test_has_distinctive_token_false_for_common_words_only():
    """Непустой OCR, но ТОЛЬКО общая лексика ("вино/красное/сухое" — в каждом
    документе) — ниже медианы IDF, не различающий токен."""
    idf = _idf_for(_catalog_for_gate())
    threshold = distinctive_idf_threshold(idf)
    assert has_distinctive_token("КРАСНОЕ СУХОЕ ВИНО", idf, threshold) is False


def test_has_distinctive_token_false_for_ocr_garbage_unknown_to_catalog():
    """Мусор OCR ("AAPAY"-подобный) — токенов вообще нет в словаре каталога, idf.get
    даёт дефолт 0.0, ниже любого положительного порога."""
    idf = _idf_for(_catalog_for_gate())
    threshold = distinctive_idf_threshold(idf)
    assert has_distinctive_token("AAPAY XZQ", idf, threshold) is False


def test_has_distinctive_token_true_for_rare_winery():
    idf = _idf_for(_catalog_for_gate())
    threshold = distinctive_idf_threshold(idf)
    assert has_distinctive_token("Инкерман красное сухое", idf, threshold) is True


def test_text_score_gate_zeroes_out_nonempty_uninformative_ocr():
    """Ключевое поведение гейта: text_score() с min_token_idf для ОБЩЕЙ лексики -> 0.0,
    хотя БЕЗ гейта (min_token_idf=None) та же пара дала бы ненулевой fuzzy-скор."""
    catalog = _catalog_for_gate()
    idf = _idf_for(catalog)
    threshold = distinctive_idf_threshold(idf)
    ocr = "красное сухое вино"  # только общая лексика, есть в ОБОИХ кандидатах ниже
    ungated = text_score(ocr, catalog["a"], idf)
    gated = text_score(ocr, catalog["a"], idf, min_token_idf=threshold)
    assert ungated > 0.0  # без гейта сигнал есть (пересечение по общим токенам + fuzzy)
    assert gated == 0.0  # с гейтом — трактуется как отсутствие сигнала


def test_text_score_gate_preserves_signal_for_distinctive_ocr():
    catalog = _catalog_for_gate()
    idf = _idf_for(catalog)
    threshold = distinctive_idf_threshold(idf)
    ocr = "Инкерман красное сухое"
    gated = text_score(ocr, catalog["a"], idf, min_token_idf=threshold)
    assert gated > 0.0


def test_rerank_top_k_nonempty_uninformative_ocr_preserves_cv_order_with_gate():
    """Расширение брифового теста "пустой OCR -> порядок CV" на РЕАЛЬНЫЙ найденный
    случай: OCR НЕ пуст, но несёт только общую лексику каталога — с гейтом ведёт себя
    так же безопасно, как совсем пустая строка (без гейта — НЕ гарантировано, см.
    test_text_score_gate_zeroes_out_nonempty_uninformative_ocr)."""
    catalog = _catalog_for_gate()
    idf = _idf_for(catalog)
    threshold = distinctive_idf_threshold(idf)
    ranked = [("a", 0.90), ("b", 0.85), ("c", 0.80)]
    result = rerank_top_k(ranked, "красное сухое вино", catalog, idf, k=3, w=5.0, min_token_idf=threshold)
    assert [slug for slug, _ in result] == ["a", "b", "c"]


def test_rerank_top_k_gate_still_promotes_on_real_distinctive_signal():
    catalog = _catalog_for_gate()
    idf = _idf_for(catalog)
    threshold = distinctive_idf_threshold(idf)
    ranked = [("a", 0.90), ("b", 0.89)]  # CV чуть предпочитает "a" (Инкерман)
    result = rerank_top_k(ranked, "Массандра красное сухое", catalog, idf, k=2, w=1.0, min_token_idf=threshold)
    assert [slug for slug, _ in result][0] == "b"  # OCR однозначно называет Массандру


def test_rerank_top_k_default_min_token_idf_none_is_backward_compatible():
    """`min_token_idf` по умолчанию `None` — гейт выключен, поведение как до его
    введения (обратная совместимость с уже принятым API)."""
    catalog = _catalog_for_gate()
    idf = _idf_for(catalog)
    ranked = [("a", 0.90), ("b", 0.85), ("c", 0.80)]
    without_kw = rerank_top_k(ranked, "красное сухое вино", catalog, idf, k=3, w=1.0)
    with_explicit_none = rerank_top_k(ranked, "красное сухое вино", catalog, idf, k=3, w=1.0, min_token_idf=None)
    assert without_kw == with_explicit_none
