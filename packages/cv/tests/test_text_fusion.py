"""Тесты cv/text_fusion.py (agents/G7-text-fusion.md) — перенос прототипа
qa/text_v2.py (гомоглифы, потокенная нечёткость, сахар) + слияние CV+текст.
Чистые юниты, без энкодера/OCR/Qdrant (та же дисциплина, что test_text_rerank.py)
— `fuse()`/`TextIndexV2` работают на голых числах/строках, `ImageIndex.search_fusion()`
(живой Qdrant) тестируется отдельно в test_index.py.
"""
from __future__ import annotations

import csv

import pytest

from cv.text_fusion import (
    DEFAULT_ANN_TOP_K,
    DEFAULT_CV_FLOOR,
    DEFAULT_CV_PAD,
    DEFAULT_GAP_FLOOR,
    DEFAULT_TEXT_TOP_N,
    DEFAULT_W,
    FUSION_FIELDS,
    TextIndexV2,
    fuse,
    homoglyph_variant,
    load_catalog_index,
    query_tokens,
    sugar_of,
    text_top_slugs_for_ocr,
    token_sim,
    top_text_slugs,
)
from cv.text_rerank import CatalogText

# --------------------------------------------------------------------------------------
# Гомоглифы OCR (докстринг модуля, п.2)
# --------------------------------------------------------------------------------------


def test_homoglyph_variant_mixed_cyrillic_and_latin_swaps_latin_doubles():
    """ДЕHИCOB — H и C латиница внутри кириллического слова -> ДЕНИСОВ."""
    assert homoglyph_variant("ДЕHИCOB") == "ДЕНИСОВ"


def test_homoglyph_variant_pure_latin_doubles_becomes_cyrillic():
    assert homoglyph_variant("CAMAPA") == "САМАРА"
    # "A" двойник только "А" (не "Я" — вне таблицы _UP) — механическая замена
    # по таблице, не восстановление орфографии: OCR тоже путает оба "А".
    assert homoglyph_variant("KPACHAA") == "КРАСНАА"


def test_homoglyph_variant_cyrillic_with_digit_double():
    """PO3E — кириллица с цифрой-двойником (3->З) внутри -> РОЗЕ."""
    assert homoglyph_variant("PO3E") == "РОЗЕ"


def test_homoglyph_variant_none_for_plain_ascii_word():
    """Обычное латинское слово без двойниковых заглавных (строчные буквы вне
    таблицы двойников) — замена неприменима."""
    assert homoglyph_variant("chateau") is None


def test_homoglyph_variant_none_for_plain_cyrillic_word():
    assert homoglyph_variant("вино") is None


# --------------------------------------------------------------------------------------
# query_tokens: сахар EN->RU, гомоглифы, фильтр коротких/цифровых токенов
# --------------------------------------------------------------------------------------


def test_query_tokens_replaces_english_sugar_markers():
    assert "suhoe" in query_tokens("Wine dry")
    assert "polusuhoe" in query_tokens("semi-dry wine")
    assert "bryut" in query_tokens("Brut")
    # semi-dry не должен ЕЩЁ ДАТЬ "suhoe" отдельно (докстринг: замена, не довесок)
    assert "suhoe" not in query_tokens("semi-dry wine")


def test_query_tokens_adds_homoglyph_variant_alongside_original():
    toks = query_tokens("CAMAPA")
    assert "samara" in toks  # транслит гомоглиф-варианта (САМАРА -> samara)


def test_query_tokens_keeps_year_even_if_short():
    assert "2022" in query_tokens("вино 2022 урожай")


def test_query_tokens_drops_non_year_digit_tokens():
    assert not any(t.isdigit() for t in query_tokens("750 мл вино"))


def test_query_tokens_drops_short_alpha_tokens_below_three_chars():
    toks = query_tokens("ИЗ вино")
    assert "iz" not in toks and "из" not in toks


def test_query_tokens_empty_text_returns_empty_set():
    assert query_tokens("") == set()
    assert query_tokens(None) == set()  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# token_sim: потокенная нечёткость (докстринг, п.3)
# --------------------------------------------------------------------------------------


def test_token_sim_exact_match_is_one():
    assert token_sim("fanagoriya", "fanagoriya") == 1.0


def test_token_sim_short_tokens_never_fuzzy_matched():
    assert token_sim("aaa", "aab") == 0.0  # min length < 4


def test_token_sim_digit_tokens_never_fuzzy_matched():
    assert token_sim("2022", "2023") == 0.0


def test_token_sim_high_ratio_above_threshold():
    # "fanagoria"/"fanagoriya" — один символ разницы на длинной строке, ratio >= 0.8
    assert token_sim("fanagoria", "fanagoriya") >= 0.8


def test_token_sim_prefix_truncation_gets_fixed_bonus():
    """Обрезка OCR (docstring модуля: "CKАЛИСТ->скалистый") — короткий префикс
    ratio ниже 0.8 напрямую, но один токен — префикс другого, длина обоих >= 5
    -> фиксированный бонус 0.85 (не сырой ratio, тот ушёл бы ниже 0.8)."""
    q, c = "skali", "skalistyywinelabelxyz"
    assert token_sim(q, c) == 0.85


def test_token_sim_close_prefix_uses_raw_ratio_not_bonus_when_ratio_already_high():
    """Когда сырой ratio УЖЕ >= 0.8 (короткая "обрезка" на длинном общем
    префиксе), возвращается сам ratio, не фиксированный 0.85 — бонус только
    подстраховка для случаев, где ratio ниже порога."""
    assert token_sim("skalist", "skalistyy") == pytest.approx(0.875)


def test_token_sim_unrelated_tokens_score_zero():
    assert token_sim("fanagoriya", "massandra") == 0.0


# --------------------------------------------------------------------------------------
# sugar_of: канонический маркер сахара (докстринг, п.4)
# --------------------------------------------------------------------------------------


def test_sugar_of_from_slug_keyword():
    assert sugar_of("relikta-polusuhoe-123", [], "") == "polusuhoe"
    assert sugar_of("igristoe-bryut-45", [], "") == "bryut"


def test_sugar_of_from_slug_suhoe_sladkoe_prefix_pattern():
    assert sugar_of("vino-suhoe-14", [], "") == "suhoe"
    assert sugar_of("vino-sladkoe-16", [], "") == "sladkoe"


def test_sugar_of_falls_back_to_photo_names():
    assert sugar_of("no-sugar-in-slug", ["Вино П.сух Резерв"], "") == "polusuhoe"


def test_sugar_of_falls_back_to_name_when_photo_names_empty():
    assert sugar_of("no-sugar-in-slug", [], "Вино экстра брют") == "ekstra bryut"


def test_sugar_of_empty_when_no_signal_anywhere():
    assert sugar_of("plain-slug", ["Просто фото"], "Просто вино") == ""


# --------------------------------------------------------------------------------------
# TextIndexV2: конструктор (idf/vocab) + scores()
# --------------------------------------------------------------------------------------


def _catalog(entries: dict[str, dict[str, str]]) -> dict[str, CatalogText]:
    return {slug: CatalogText(slug=slug, **fields) for slug, fields in entries.items()}


def test_text_index_v2_scores_empty_query_returns_zeros():
    catalog = _catalog({"a": {"name": "Шато Восход"}, "b": {"name": "Долина Смыслов"}})
    idx = TextIndexV2(catalog, fields=("name",))
    rec, mass = idx.scores("")
    assert rec == [0.0, 0.0]
    assert mass == [0.0, 0.0]


def test_text_index_v2_scores_exact_token_match_gives_full_recall():
    catalog = _catalog({
        "a": {"name": "Фанагория", "winery": "Фанагория"},
        "b": {"name": "Массандра", "winery": "Массандра"},
    })
    idx = TextIndexV2(catalog, fields=("name", "winery"))
    rec, mass = idx.scores("ФАНАГОРИЯ красное сухое")
    assert rec[0] == pytest.approx(1.0)
    assert mass[0] > mass[1]  # "a" совпал, "b" — нет


def test_text_index_v2_extra_fields_participate_in_scoring():
    """`extra` (category/sugar) — поля вне CatalogText, докстринг класса."""
    catalog = _catalog({"a": {"name": "Вино А"}, "b": {"name": "Вино Б"}})
    extra = {"a": {"category": "Красное", "sugar": "suhoe"}, "b": {"category": "Белое", "sugar": "sladkoe"}}
    idx = TextIndexV2(catalog, fields=("category", "sugar"), extra=extra)
    rec, mass = idx.scores("красное сухое")
    assert mass[0] > mass[1]


def test_text_index_v2_fuzzy_typo_still_scores_via_token_sim():
    catalog = _catalog({"a": {"winery": "Фанагория"}, "b": {"winery": "Массандра"}})
    idx = TextIndexV2(catalog, fields=("winery",))
    rec, mass = idx.scores("ФАНАГОРИЙ")  # опечатка на конце
    assert mass[0] > mass[1]
    assert rec[0] > 0.5


# --------------------------------------------------------------------------------------
# load_catalog_index: парсинг CSV каталога кейса (name/winery/grape/category/sugar)
# --------------------------------------------------------------------------------------

_CSV_FIELDS = [
    "Название вина", "Категория", "Цвет", "Регион", "Сорт винограда",
    "Описание", "Винодельня", "Slug", "Название фото",
]


def _write_case_csv(tmp_path, rows: list[dict[str, str]]):
    path = tmp_path / "catalog.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def test_load_catalog_index_reads_category_and_sugar_from_csv(tmp_path):
    csv_path = _write_case_csv(tmp_path, [
        {"Название вина": "Жемчужная 9", "Категория": "Белое", "Винодельня": "Кубань-Вино",
         "Slug": "zhemchuzhnaya-9-suhoe", "Название фото": "Жемчужная 9 сухое"},
    ])
    load_catalog_index.cache_clear()
    idx = load_catalog_index(str(csv_path))
    assert idx.slugs == ["zhemchuzhnaya-9-suhoe"]
    rec, mass = idx.scores("белое сухое")
    assert mass[0] > 0  # категория "Белое" + сахар из слага "suhoe" оба дали сигнал


def test_load_catalog_index_missing_file_degrades_to_empty_index():
    load_catalog_index.cache_clear()
    idx = load_catalog_index("/no/such/catalog.csv")
    assert idx.slugs == []
    assert idx.scores("что угодно") == ([], [])


def test_load_catalog_index_caches_by_path_string(tmp_path):
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "one-slug", "Название вина": "Одно вино", "Винодельня": "В"},
    ])
    load_catalog_index.cache_clear()
    first = load_catalog_index(str(csv_path))
    second = load_catalog_index(str(csv_path))
    assert first is second  # тот же объект — CSV не перечитан


def test_load_catalog_index_duplicate_slug_rows_keep_first_nonempty_field_per_column(tmp_path):
    """Slug дублируется по фото (brief п.1, та же конвенция что tr.load_catalog_text) —
    Категория берётся из первой строки, где она вообще заполнена."""
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "dup-slug", "Название вина": "Вино", "Категория": ""},
        {"Slug": "dup-slug", "Название вина": "Вино", "Категория": "Красное"},
    ])
    load_catalog_index.cache_clear()
    idx = load_catalog_index(str(csv_path))
    rec, mass = idx.scores("красное")
    assert mass[0] > 0


# --------------------------------------------------------------------------------------
# top_text_slugs / text_top_slugs_for_ocr
# --------------------------------------------------------------------------------------


def test_top_text_slugs_ranks_by_mass_descending_and_excludes_zero():
    idx = TextIndexV2(_catalog({
        "a": {"name": "Фанагория Красное"}, "b": {"name": "Фанагория"}, "c": {"name": "Совсем другое"},
    }), fields=("name",))
    _, mass = idx.scores("фанагория красное")
    top = top_text_slugs(idx, mass, limit=2)
    assert top == ["a", "b"]  # "c" — нулевая масса, не кандидат вовсе


def test_top_text_slugs_respects_limit():
    idx = TextIndexV2(_catalog({f"s{i}": {"name": "Фанагория"} for i in range(5)}), fields=("name",))
    _, mass = idx.scores("фанагория")
    assert len(top_text_slugs(idx, mass, limit=2)) == 2


def test_text_top_slugs_for_ocr_empty_index_returns_empty_list():
    idx = TextIndexV2({}, fields=("name",))
    assert text_top_slugs_for_ocr(idx, "фанагория", limit=30) == []


# --------------------------------------------------------------------------------------
# fuse(): формула final=cv+w*rel, заглушка cv_top1-pad, family-gap, гейт
# --------------------------------------------------------------------------------------


def _text_index_two_docs() -> TextIndexV2:
    return TextIndexV2(_catalog({
        "target": {"name": "Фанагория Крю Лермонт"}, "other": {"name": "Массандра Мускатель"},
    }), fields=("name",))


def test_fuse_empty_cv_scores_short_circuits_to_empty_result():
    idx = _text_index_two_docs()
    result = fuse({}, idx, "фанагория")
    assert result.ranked == []
    assert result.gap is None
    assert result.confident is False


def test_fuse_final_score_arithmetic_matches_cv_plus_w_times_rel():
    idx = _text_index_two_docs()
    cv_scores = {"target": 0.80, "other": 0.80}
    result = fuse(cv_scores, idx, "фанагория крю лермонт", w=0.2)
    by_slug = {c.slug: c for c in result.ranked}
    assert by_slug["target"].rel == pytest.approx(1.0)  # лучший текстовый кандидат в этом запросе
    assert by_slug["target"].final_score == pytest.approx(0.80 + 0.2 * 1.0)
    assert by_slug["other"].rel == pytest.approx(0.0)
    assert by_slug["other"].final_score == pytest.approx(0.80)


def test_fuse_ranks_target_above_higher_cv_but_textually_silent_rival():
    """Основной сценарий брифа: текст поднимает слаг, которого CV поставила НИЖЕ."""
    idx = _text_index_two_docs()
    cv_scores = {"target": 0.75, "other": 0.80}  # CV предпочла "other"
    result = fuse(cv_scores, idx, "фанагория крю лермонт", w=0.2)
    assert result.ranked[0].slug == "target"  # текст (rel=1.0) перевесил разрыв по CV (0.05)


def test_fuse_pads_candidate_without_cv_reference_using_cv_top1_minus_pad():
    """brief п.6: слаг без эталона в индексе (не в cv_scores) — CV-заглушка
    cv_top1 - CV_PAD, иначе текст не может его поднять вообще."""
    idx = TextIndexV2(_catalog({
        "has-ref": {"name": "Обычное вино"}, "no-ref": {"name": "Табия Резерв Уникальное"},
    }), fields=("name",))
    cv_scores = {"has-ref": 0.90}  # "no-ref" отсутствует — нет эталона в индексе
    result = fuse(cv_scores, idx, "табия резерв уникальное", cv_pad=0.03, text_top_n=10)
    by_slug = {c.slug: c for c in result.ranked}
    assert "no-ref" in by_slug
    assert by_slug["no-ref"].cv_score == pytest.approx(0.90 - 0.03)


def test_fuse_gate_requires_both_cv_floor_and_gap_floor():
    idx = _text_index_two_docs()
    family_by_slug: dict[str, str] = {}

    # CV top-1 ниже пола -> не уверенно, даже с большим отрывом.
    low_cv = fuse({"target": 0.5, "other": 0.1}, idx, "фанагория крю лермонт",
                  family_by_slug=family_by_slug, cv_floor=0.80, gap_floor=0.03)
    assert low_cv.confident is False

    # CV top-1 выше пола, но конкурент СЛИШКОМ близко (gap < floor) -> не уверенно.
    idx_close = TextIndexV2(_catalog({
        "target": {"name": "Вино А"}, "other": {"name": "Вино А"},  # текст неразличим
    }), fields=("name",))
    close_gap = fuse({"target": 0.90, "other": 0.885}, idx_close, "вино а",
                      family_by_slug=family_by_slug, cv_floor=0.80, gap_floor=0.03)
    assert close_gap.gap == pytest.approx(0.015, abs=1e-6)
    assert close_gap.confident is False

    # Оба условия выполнены -> уверенно.
    ok = fuse({"target": 0.90, "other": 0.5}, idx, "фанагория крю лермонт",
              family_by_slug=family_by_slug, cv_floor=0.80, gap_floor=0.03)
    assert ok.confident is True


def test_fuse_null_gap_dominance_still_requires_cv_floor():
    """Вся кандидатная вселенная — одна near-dup семья (или единственный
    кандидат) -> gap=None (доминирование, та же трактовка, что v0.4.7 §2 для
    обычного гейта) — confident зависит ТОЛЬКО от cv_floor в этом случае."""
    idx = TextIndexV2(_catalog({"solo": {"name": "Одинокое вино"}}), fields=("name",))

    high_cv = fuse({"solo": 0.90}, idx, "одинокое вино", cv_floor=0.80, gap_floor=0.03)
    assert high_cv.gap is None
    assert high_cv.confident is True

    low_cv = fuse({"solo": 0.5}, idx, "одинокое вино", cv_floor=0.80, gap_floor=0.03)
    assert low_cv.gap is None
    assert low_cv.confident is False


def test_fuse_family_gap_skips_same_family_member_for_gap_computation():
    idx = TextIndexV2(_catalog({
        "fam-a": {"name": "Вино"}, "fam-b": {"name": "Вино"}, "stranger": {"name": "Вино"},
    }), fields=("name",))
    cv_scores = {"fam-a": 0.90, "fam-b": 0.89, "stranger": 0.87}
    family_by_slug = {"fam-a": "fam1", "fam-b": "fam1"}
    result = fuse(cv_scores, idx, "", family_by_slug=family_by_slug, gap_floor=0.03)
    assert result.ranked[0].slug == "fam-a"
    # gap обязан пропустить fam-b (та же семья) и указать на первого чужака.
    assert result.gap == pytest.approx(result.ranked[0].final_score - next(
        c.final_score for c in result.ranked if c.slug == "stranger"
    ))


def test_fuse_ranked_covers_cv_union_text_top_n_universe():
    """Кандидатная вселенная — объединение cv_scores и текстовых top-N, не
    пересечение: слаг из cv_scores без текстового сигнала всё равно остаётся
    (rel=0), а текстовый кандидат вне cv_scores получает заглушку (см. тест
    заглушки выше) — здесь просто проверяем, что оба класса присутствуют."""
    idx = TextIndexV2(_catalog({
        "cv-only": {"name": "Ничего общего с запросом"}, "text-only": {"name": "Фанагория Крю"},
    }), fields=("name",))
    result = fuse({"cv-only": 0.85}, idx, "фанагория крю", text_top_n=5)
    slugs = {c.slug for c in result.ranked}
    assert slugs == {"cv-only", "text-only"}


# --------------------------------------------------------------------------------------
# Дефолты — пин значений брифа (agents/G7-text-fusion.md, "Параметры")
# --------------------------------------------------------------------------------------


def test_default_constants_pin_brief_values():
    assert DEFAULT_W == 0.2
    assert DEFAULT_CV_PAD == 0.03
    assert DEFAULT_GAP_FLOOR == 0.03
    assert DEFAULT_CV_FLOOR == 0.80
    assert DEFAULT_ANN_TOP_K == 50
    assert DEFAULT_TEXT_TOP_N == 30
    assert FUSION_FIELDS == ("name", "winery", "grape", "category", "sugar")
