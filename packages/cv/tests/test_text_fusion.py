"""Тесты cv/text_fusion.py (agents/G7-text-fusion.md) — перенос прототипа
qa/text_v2.py (гомоглифы, потокенная нечёткость, сахар) + слияние CV+текст.
Чистые юниты, без энкодера/OCR/Qdrant (та же дисциплина, что test_text_rerank.py)
— `fuse()`/`TextIndexV2` работают на голых числах/строках, `ImageIndex.search_fusion()`
(живой Qdrant) тестируется отдельно в test_index.py.
"""
from __future__ import annotations

import csv
import json

import pytest

from cv import config
from cv.text_fusion import (
    DEFAULT_ANN_TOP_K,
    DEFAULT_CV_FLOOR,
    DEFAULT_CV_PAD,
    DEFAULT_GAP_FLOOR,
    DEFAULT_TEXT_TOP_N,
    DEFAULT_W,
    FUSION_FIELDS,
    TextIndexV2,
    default_winery_aliases_path,
    fuse,
    homoglyph_variant,
    load_catalog_index,
    load_winery_alias_groups,
    load_winery_index,
    query_tokens,
    sugar_of,
    text_top_slugs_for_ocr,
    token_sim,
    top_text_slugs,
    text_color,
    color_by_slug,
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
# "i"->"и" (agents/H2-rapidocr-multiscale.md, текст RapidOCR) — вторая буква без
# кириллического аналога в исходном наборе _UP/_LOW/_DIG, найдена на «Py6iH»=«Рубин».
# --------------------------------------------------------------------------------------


def test_homoglyph_variant_latin_i_doubles_to_cyrillic_i_kratkoe():
    """Реальный замер (RapidOCR, agents/H2-rapidocr-multiscale.md): «Py6iH» — P/H
    уже латинские двойники (_UP), 6 — цифровой двойник (_DIG), "i" без своей пары
    вообще не входил бы ни в один алфавит двойников, и замена всего токена
    срывалась бы (условие «ВСЕ буквы — двойники» не выполнялось) -> «Рубин»."""
    assert homoglyph_variant("Py6iH") == "РубиН"


def test_query_tokens_recognizes_rubin_through_i_homoglyph():
    """Итоговый эффект на токенизации запроса (не только на самом
    homoglyph_variant()) — транслит гомоглиф-варианта даёт токен "rubin", как и
    прямая токенизация слова «Рубин» (см. cv.text_rerank.tokenize)."""
    assert "rubin" in query_tokens("Py6iH")


def test_homoglyph_variant_pinot_untouched_by_i_to_cyrillic_i():
    """«Pinot» несёт "i", но остальные буквы (n, t) вне таблиц двойников — та же
    дисциплина, что и раньше: вариант ТОЛЬКО когда ВСЕ буквы токена — двойники
    (или токен смешанный кириллица+латиница), не при частичном совпадении.
    Регресс-гарантия: добавление "i"->"и" не должно тронуть обычные латинские
    слова вида названий сортов винограда."""
    assert homoglyph_variant("Pinot") is None
    assert query_tokens("Pinot") == {"pinot"}


# --------------------------------------------------------------------------------------
# Греческие двойники OCR (agents/H1-cpu-path.md, живые фото 21.09) — Λ/Γ/Π/Δ/Φ, ровно
# пять кириллических букв БЕЗ латинского двойника, но графически совпадающих с
# греческими заглавными.
# --------------------------------------------------------------------------------------


def test_homoglyph_variant_greek_uppercase_doubles_become_cyrillic():
    """Реальный замер: «OΛEΓ» (O/E — уже латинские двойники, Λ/Γ — греческие) -> ОЛЕГ."""
    assert homoglyph_variant("OΛEΓ") == "ОЛЕГ"


def test_homoglyph_variant_pure_greek_doubles_word():
    """Все пять греческих двойников разом, без примеси латиницы."""
    assert homoglyph_variant("ΛΓΠΔΦ") == "ЛГПДФ"


def test_homoglyph_variant_unmapped_greek_letter_abstains():
    """Ω — греческая буква ВНЕ подтверждённой пятёрки: одна известная (Λ) не даёт
    права гадать про соседнюю неизвестную (Ω) — воздержание (None), не частичная
    или ошибочная подстановка (та же дисциплина, что и у _UP/_DIG: только
    подтверждённые измерением двойники, см. докстринг модуля)."""
    assert homoglyph_variant("ΛΩ") is None


def test_homoglyph_variant_greek_untouched_in_mixed_cyrillic_branch():
    """Регресс: смешанная кириллица+латиница (`has_cyr and has_lat`) — ветка ДО
    этой правки, `_GREEK` в ней не участвует намеренно (греческие двойники —
    измеренный сценарий ТОЛЬКО для латински-читаемых обрывков вида "OΛEΓ", не для
    смеси с уже-кириллицей). Греческая буква внутри такого токена просто проходит
    НЕИЗМЕНЁННОЙ (как любой другой неизвестный символ этой ветки), остальные
    правила (латинские двойники) работают как раньше."""
    assert homoglyph_variant("винΛo") == "винΛо"  # только "o"->"о" (_LOW), Λ не тронут


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


def test_query_tokens_adds_greek_homoglyph_variant_alongside_original():
    """agents/H1-cpu-path.md: тот же путь, что и латинские двойники — гомоглиф-
    вариант ("ОЛЕГ") транслитерируется наравне с сырым токеном."""
    toks = query_tokens("OΛEΓ")
    assert "oleg" in toks  # транслит греческого гомоглиф-варианта (ОЛЕГ -> oleg)


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
# Гейт «не подтверждена винодельня» (agents/H1-cpu-path.md, живые фото 21.09) —
# третья накопительная поправка CPU-пути, offline 87.1% -> 88.7% top-1.
# --------------------------------------------------------------------------------------


def _winery_catalog() -> tuple[TextIndexV2, TextIndexV2]:
    """(text_index на name+winery, winery_index ТОЛЬКО на winery) — тот же каталог
    для обоих, как строит `app/cv/service.py::_run_photo_scan_fusion` (два разных
    `_fusion_*_index()` из одного CSV)."""
    catalog = _catalog({
        "confirmed": {"name": "Крю Лермонт", "winery": "Фанагория"},
        "unconfirmed": {"name": "Крю Лермонт Резерв", "winery": "Другая Винодельня"},
    })
    return TextIndexV2(catalog, fields=("name", "winery")), TextIndexV2(catalog, fields=("winery",))


def test_fuse_winery_gate_has_no_effect_when_winery_index_not_passed():
    """Регресс: НЕ передавать `winery_index` (дефолт `None`) — бит-в-бит поведение
    до этой правки, никакого урезания, даже если бы кандидаты формально были
    'неподтверждёнными'."""
    text_idx, _winery_idx = _winery_catalog()
    cv_scores = {"confirmed": 0.80, "unconfirmed": 0.80}
    result = fuse(cv_scores, text_idx, "фанагория крю лермонт", w=0.2)
    by_slug = {c.slug: c for c in result.ranked}
    assert by_slug["unconfirmed"].rel == pytest.approx(0.587291291059816)


def test_fuse_winery_gate_has_no_effect_with_default_weight_even_if_index_passed():
    """`unconfirmed_winery_w` дефолт (`DEFAULT_UNCONFIRMED_WINERY_W` = 1.0, "как
    сейчас") — передать `winery_index` БЕЗ явного веса не должно ничего менять."""
    text_idx, winery_idx = _winery_catalog()
    cv_scores = {"confirmed": 0.80, "unconfirmed": 0.80}
    gated = fuse(cv_scores, text_idx, "фанагория крю лермонт", w=0.2, winery_index=winery_idx)
    plain = fuse(cv_scores, text_idx, "фанагория крю лермонт", w=0.2)
    by_slug_gated = {c.slug: c for c in gated.ranked}
    by_slug_plain = {c.slug: c for c in plain.ranked}
    for slug in ("confirmed", "unconfirmed"):
        assert by_slug_gated[slug].rel == pytest.approx(by_slug_plain[slug].rel)


def test_fuse_halves_rel_for_candidate_with_unconfirmed_winery():
    """Основной сценарий брифа: запрос упоминает винодельню ОДНОГО кандидата
    ("фанагория") — тот остаётся с полным `rel`; конкурент, чья винодельня НЕ
    упомянута (recall=0.0 по полю winery, хоть и делит общие токены названия),
    получает `rel`, урезанный `unconfirmed_winery_w`."""
    text_idx, winery_idx = _winery_catalog()
    cv_scores = {"confirmed": 0.80, "unconfirmed": 0.80}
    result = fuse(
        cv_scores, text_idx, "фанагория крю лермонт", w=0.2,
        winery_index=winery_idx, unconfirmed_winery_w=0.5,
    )
    by_slug = {c.slug: c for c in result.ranked}
    assert by_slug["confirmed"].rel == pytest.approx(1.0)  # винодельня подтверждена -> не урезан
    assert by_slug["unconfirmed"].rel == pytest.approx(0.293645645529908)  # ровно половина plain-rel
    assert by_slug["confirmed"].final_score > by_slug["unconfirmed"].final_score


def test_fuse_winery_gate_keeps_final_score_invariant():
    """`final_score == cv_score + w*rel` держится ДАЖЕ когда `rel` урезан гейтом —
    урезание сидит внутри `rel`, не отдельным множителем поверх формулы (см.
    докстринг `fuse()`/`FusedCandidate.rel`)."""
    text_idx, winery_idx = _winery_catalog()
    cv_scores = {"confirmed": 0.80, "unconfirmed": 0.80}
    result = fuse(
        cv_scores, text_idx, "фанагория крю лермонт", w=0.2,
        winery_index=winery_idx, unconfirmed_winery_w=0.5,
    )
    for c in result.ranked:
        assert c.final_score == pytest.approx(c.cv_score + 0.2 * c.rel)


def test_fuse_winery_gate_recall_exactly_at_floor_counts_as_confirmed():
    """brief: 'recall >= 0.5' подтверждает — граница НЕ урезается. Конструкция:
    винодельня из двух токенов РАВНОГО IDF (по одному в каждом из двух слагов
    каталога), запрос называет РОВНО один из них -> recall ТОЧНО 0.5."""
    catalog = _catalog({"a": {"winery": "Альфа Бета"}, "b": {"winery": "Гамма Дельта"}})
    text_idx = TextIndexV2(catalog, fields=("winery",))
    winery_idx = TextIndexV2(catalog, fields=("winery",))
    rec, _mass = winery_idx.scores("альфа")
    assert dict(zip(winery_idx.slugs, rec))["a"] == 0.5  # граница ровно на полу, не окрест него

    result = fuse(
        {"a": 0.80, "b": 0.80}, text_idx, "альфа", w=0.2,
        winery_index=winery_idx, unconfirmed_winery_w=0.5, winery_recall_floor=0.5,
    )
    by_slug = {c.slug: c for c in result.ranked}
    assert by_slug["a"].rel == pytest.approx(1.0)  # recall==floor -> подтверждена, НЕ урезана


def test_fuse_winery_gate_confirmation_uses_recall_not_raw_mass():
    """Гейт использует RECALL (долю) индекса winery, не абсолютную IDF-массу:
    'long-winery' (винодельня из ПЯТИ токенов, запрос называет только один) и
    'short-winery' (винодельня из ОДНОГО токена, запрос называет его целиком)
    набирают РАВНУЮ абсолютную массу по полю winery (один и тот же токен
    matched с одинаковым IDF) — но recall различается кардинально (0.2 против
    1.0). Если бы гейт ошибочно сравнивал массу, а не recall, оба считались бы
    одинаково (не)подтверждёнными — на деле подтверждён только 'short-winery'."""
    catalog = _catalog({
        "long-winery": {"name": "Вино", "winery": "Первая Вторая Третья Четвертая Пятая"},
        "short-winery": {"name": "Вино", "winery": "Шестая"},
    })
    text_idx = TextIndexV2(catalog, fields=("name", "winery"))
    winery_idx = TextIndexV2(catalog, fields=("winery",))

    rec, mass = winery_idx.scores("первая шестая")
    rec_by_slug, mass_by_slug = dict(zip(winery_idx.slugs, rec)), dict(zip(winery_idx.slugs, mass))
    assert mass_by_slug["long-winery"] == pytest.approx(mass_by_slug["short-winery"])  # равная масса
    assert rec_by_slug["long-winery"] == pytest.approx(0.2)  # но recall РАЗНЫЙ
    assert rec_by_slug["short-winery"] == pytest.approx(1.0)

    result = fuse(
        {"long-winery": 0.80, "short-winery": 0.80}, text_idx, "первая шестая", w=0.2,
        winery_index=winery_idx, unconfirmed_winery_w=0.5,
    )
    by_slug = {c.slug: c for c in result.ranked}
    assert by_slug["short-winery"].final_score > by_slug["long-winery"].final_score


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


# --------------------------------------------------------------------------------------
# 22.09 (оркестратор, разбор промахов стенда hack-v6): полная греческая таблица,
# перевод греческих букв ДО разбора токена; штраф за противоречие цвета этикетки
# --------------------------------------------------------------------------------------


def test_query_tokens_mixed_cyrillic_and_greek_token_reads_oleg():
    """Реальный текст RapidOCR: «ОΛΕΓ» — кириллическая О, греческие Λ, Ε, Γ. Раньше смешанный
    кириллица+греческий токен не брала ни одна ветка homoglyph_variant(); два фото «Табия —
    Олег» терялись."""
    assert "oleg" in query_tokens("ТАБИЯ ОΛΕΓ")
    assert "oleg" in query_tokens("OΛEΓ")  # прежний случай — латинские O/E + греческие Λ/Γ


def test_text_color_needs_exactly_one_color_word():
    assert text_color("МУСКАТЕЛЬ БЕЛЫЙ 2023") == "white"
    assert text_color("Blanc de Blancs white") == "white"
    assert text_color("Красная стрелка розовое") is None  # два цвета — не гадаем
    assert text_color("") is None


def test_color_by_slug_reads_category_field():
    idx = TextIndexV2(
        _catalog({"w": {"name": "Мускатель белый"}, "r": {"name": "Мускатель розовый"}, "o": {"name": "Оранж"}}),
        fields=("name",),
        extra={"w": {"category": "Белое", "sugar": ""}, "r": {"category": "Розовое", "sugar": ""}, "o": {"category": "Оранжевое", "sugar": ""}},
    )
    assert color_by_slug(idx) == {"w": "white", "r": "rose"}  # оранжевое — без цвета


def _two_wines_same_name_different_color():
    """Одна линейка, одинаковое НАЗВАНИЕ, цвет — только в поле «Категория» каталога (как у
    «Мускатель» Массандры до чистки названий): текст обоих кандидатов совпадает, различить
    их может только слово цвета на этикетке."""
    return TextIndexV2(
        _catalog({"w": {"name": "Мускатель", "winery": "Массандра"},
                  "r": {"name": "Мускатель", "winery": "Массандра"}}),
        fields=("name", "winery"),
        extra={"w": {"category": "Белое", "sugar": ""}, "r": {"category": "Розовое", "sugar": ""}},
    )


def test_fuse_color_penalty_demotes_candidate_contradicting_label_color():
    idx = _two_wines_same_name_different_color()
    cv_scores = {"w": 0.85, "r": 0.87}
    text = "МАССАНДРА МУСКАТЕЛЬ БЕЛЫЙ 2023"
    without = fuse(cv_scores, idx, text, w=0.3)
    assert without.ranked[0].slug == "r"  # текст одинаков, без штрафа CV решает в пользу розового
    with_pen = fuse(cv_scores, idx, text, w=0.3, colors=color_by_slug(idx), color_penalty=0.05)
    assert with_pen.ranked[0].slug == "w"
    by_slug = {c.slug: c for c in with_pen.ranked}
    assert by_slug["r"].final_score == pytest.approx(without.ranked[0].final_score - 0.05)


def test_fuse_color_penalty_inactive_without_color_word_or_zero_penalty():
    idx = _two_wines_same_name_different_color()
    colors = color_by_slug(idx)
    a = fuse({"w": 0.85, "r": 0.87}, idx, "МУСКАТЕЛЬ 2023", w=0.3, colors=colors, color_penalty=0.05)
    b = fuse({"w": 0.85, "r": 0.87}, idx, "МУСКАТЕЛЬ БЕЛЫЙ 2023", w=0.3, colors=colors, color_penalty=0.0)
    assert a.ranked[0].slug == "r" and b.ranked[0].slug == "r"


# --------------------------------------------------------------------------------------
# Алиасы написания винодельни (agents/ML-1-*.md, задача 2) — load_winery_alias_groups() /
# default_winery_aliases_path() / load_winery_index(). Реальный кейс — слаг 93.97
# (golubitskoe-estate-chardonnay), reports/ml-lead-plan.md: каталог пишет ОДНОГО
# производителя ДВУМЯ строками поля «Винодельня», гейт «не подтверждена винодельня»
# наказывает истину по её собственной строке.
# --------------------------------------------------------------------------------------


def _write_aliases(tmp_path, groups: list):
    path = tmp_path / "winery_aliases.json"
    path.write_text(json.dumps({"groups": groups}, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_winery_alias_groups_missing_file_returns_empty(tmp_path):
    assert load_winery_alias_groups(tmp_path / "does-not-exist.json") == []


def test_load_winery_alias_groups_malformed_json_returns_empty(tmp_path):
    path = tmp_path / "winery_aliases.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_winery_alias_groups(path) == []


def test_load_winery_alias_groups_non_object_json_returns_empty(tmp_path):
    path = tmp_path / "winery_aliases.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_winery_alias_groups(path) == []


def test_load_winery_alias_groups_missing_groups_key_returns_empty(tmp_path):
    path = tmp_path / "winery_aliases.json"
    path.write_text(json.dumps({"note": "нет groups"}), encoding="utf-8")
    assert load_winery_alias_groups(path) == []


def test_load_winery_alias_groups_reads_brief_example(tmp_path):
    """Ровно пример из брифа (agents/ML-1-*.md, задача 2) — Голубицкое."""
    path = _write_aliases(tmp_path, [
        {"strings": ["Поместье Голубицкое", "Golubitskoe Estate"], "anchor_token": "golubitskoe",
         "note": "один производитель, 2 vs 28 SKU в каталоге"},
    ])
    assert load_winery_alias_groups(path) == [["Поместье Голубицкое", "Golubitskoe Estate"]]


def test_load_winery_alias_groups_drops_groups_with_fewer_than_two_strings(tmp_path):
    path = _write_aliases(tmp_path, [{"strings": ["Одна Строка"]}, {"strings": []}])
    assert load_winery_alias_groups(path) == []


def test_load_winery_alias_groups_strips_and_drops_blank_or_non_string_entries(tmp_path):
    path = _write_aliases(tmp_path, [{"strings": ["  А  ", "", "  Б  ", None, 42]}])
    assert load_winery_alias_groups(path) == [["А", "Б"]]


def test_load_winery_alias_groups_ignores_malformed_group_entries(tmp_path):
    """Группа-не-словарь и "strings"-не-список просто пропускаются, не валят загрузку целиком."""
    path = _write_aliases(tmp_path, ["не словарь", {"strings": "не список"}, {"strings": ["X", "Y"]}])
    assert load_winery_alias_groups(path) == [["X", "Y"]]


def test_default_winery_aliases_path_uses_env_when_set(monkeypatch, tmp_path):
    custom = tmp_path / "custom-aliases.json"
    monkeypatch.setenv("CV_WINERY_ALIASES_JSON", str(custom))
    assert default_winery_aliases_path() == custom


def test_default_winery_aliases_path_falls_back_to_case_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("CV_WINERY_ALIASES_JSON", raising=False)
    monkeypatch.setattr(config, "CASE_DATA_DIR", tmp_path)
    assert default_winery_aliases_path() == tmp_path / "winery_aliases.json"


def test_load_winery_index_missing_aliases_file_matches_load_catalog_index_object(tmp_path):
    """brief п.3: "файл алиасов отсутствует — старое поведение" — не просто те же
    числа, а РОВНО тот же кэшированный объект `TextIndexV2` (никакой копии)."""
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "a", "Название вина": "Вино А", "Винодельня": "Фанагория"},
        {"Slug": "b", "Название вина": "Вино Б", "Винодельня": "Массандра"},
    ])
    load_catalog_index.cache_clear()
    plain = load_catalog_index(str(csv_path), fields=("winery",))
    got = load_winery_index(str(csv_path), aliases_json=tmp_path / "no-such-aliases.json")
    assert got is plain


def test_load_winery_index_empty_groups_file_matches_load_catalog_index_object(tmp_path):
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "a", "Название вина": "Вино А", "Винодельня": "Фанагория"},
    ])
    aliases_path = _write_aliases(tmp_path, [])
    load_catalog_index.cache_clear()
    plain = load_catalog_index(str(csv_path), fields=("winery",))
    got = load_winery_index(str(csv_path), aliases_json=aliases_path)
    assert got is plain


def test_load_winery_index_slug_outside_any_group_is_unaffected(tmp_path):
    """Регрессия (brief п.3): винодельня НЕ входит ни в одну группу алиасов -> её
    recall/mass остаются РОВНО теми же, что у `load_catalog_index()` без алиасов —
    не просто "top-1 совпадает", а те же числа."""
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "a", "Название вина": "Вино А", "Винодельня": "Фанагория"},
        {"Slug": "b", "Название вина": "Вино Б", "Винодельня": "Массандра"},
    ])
    aliases_path = _write_aliases(tmp_path, [{"strings": ["Совсем Другая Винодельня", "Other Winery"]}])

    load_catalog_index.cache_clear()
    plain = load_catalog_index(str(csv_path), fields=("winery",))
    aliased = load_winery_index(str(csv_path), aliases_json=aliases_path)

    plain_rec, plain_mass = plain.scores("фанагория массандра")
    aliased_rec, aliased_mass = aliased.scores("фанагория массандра")
    assert dict(zip(aliased.slugs, aliased_rec)) == dict(zip(plain.slugs, plain_rec))
    assert dict(zip(aliased.slugs, aliased_mass)) == dict(zip(plain.slugs, plain_mass))


def test_load_winery_index_merges_and_fixes_golubitskoe_style_gate_false_negative(tmp_path):
    """Основной сценарий брифа (agents/ML-1-*.md, задача 2; reports/ml-lead-plan.md,
    промах 93.97): каталог пишет ОДНОГО производителя ДВУМЯ строками «Винодельня» —
    «Поместье Голубицкое» (2 SKU, включает истину) и «Golubitskoe Estate» (28 SKU,
    включает ложный top-1). Без алиаса гейт «не подтверждена винодельня» наказывает
    ИМЕННО истину (её собственная строка не совпадает с «Golubitskoe» на этикетке
    целиком); с алиасом обе строки подтверждают друг друга — recall становится РАВНЫМ
    и проходит `winery_recall_floor` для ОБОИХ."""
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "truth", "Название вина": "Golubitskoe Estate Chardonnay", "Винодельня": "Поместье Голубицкое"},
        {"Slug": "rival", "Название вина": "Golubitskoe Estate Reserve", "Винодельня": "Golubitskoe Estate"},
    ])
    aliases_path = _write_aliases(tmp_path, [
        {"strings": ["Поместье Голубицкое", "Golubitskoe Estate"], "anchor_token": "golubitskoe"},
    ])
    query = "Golubitskoe Estate Chardonnay"  # текст этикетки (OCR/VLM), как в разборе промаха

    load_catalog_index.cache_clear()
    plain_winery = load_catalog_index(str(csv_path), fields=("winery",))
    plain_rec, _ = plain_winery.scores(query)
    plain_by_slug = dict(zip(plain_winery.slugs, plain_rec))
    assert plain_by_slug["truth"] < 0.5  # без алиаса — recall истины НИЖЕ пола (как в отчёте: 0.40)
    assert plain_by_slug["rival"] >= 0.5  # у конкурента recall полный (оба слова совпали)

    aliased_winery = load_winery_index(str(csv_path), aliases_json=aliases_path)
    aliased_rec, _ = aliased_winery.scores(query)
    aliased_by_slug = dict(zip(aliased_winery.slugs, aliased_rec))
    assert aliased_by_slug["truth"] == pytest.approx(aliased_by_slug["rival"])  # оба написания -> тот же recall
    assert aliased_by_slug["truth"] >= 0.5  # теперь подтверждена тоже

    # Практический эффект через fuse(): текстовая масса уже отдаёт победу truth (её
    # название само содержит «Golubitskoe Estate»), но БЕЗ алиаса гейт срезает это
    # преимущество до потери top-1 — С алиасом истина побеждает.
    text_index = TextIndexV2(
        _catalog({
            "truth": {"name": "Golubitskoe Estate Chardonnay", "winery": "Поместье Голубицкое"},
            "rival": {"name": "Golubitskoe Estate Reserve", "winery": "Golubitskoe Estate"},
        }),
        fields=("name", "winery"),
    )
    cv_scores = {"truth": 0.8678, "rival": 0.8676}  # CV — почти ничья, как в разборе промаха
    without_alias = fuse(
        cv_scores, text_index, query, w=0.3, winery_index=plain_winery, unconfirmed_winery_w=0.5,
    )
    with_alias = fuse(
        cv_scores, text_index, query, w=0.3, winery_index=aliased_winery, unconfirmed_winery_w=0.5,
    )
    assert without_alias.ranked[0].slug == "rival"  # баг ДО этой правки — гейт наказывает truth незаслуженно
    assert with_alias.ranked[0].slug == "truth"  # ПОСЛЕ правки — оба написания подтверждают друг друга


def test_load_winery_index_returns_new_object_when_aliases_applied(tmp_path):
    """Не мутирует закэшированный `load_catalog_index()` — когда алиас реально
    что-то меняет, `load_winery_index()` строит НОВЫЙ объект (иначе испортил бы
    результат для любого другого вызывающего кода с тем же catalog_csv)."""
    csv_path = _write_case_csv(tmp_path, [
        {"Slug": "truth", "Название вина": "Вино", "Винодельня": "Поместье Голубицкое"},
        {"Slug": "rival", "Название вина": "Вино", "Винодельня": "Golubitskoe Estate"},
    ])
    aliases_path = _write_aliases(tmp_path, [{"strings": ["Поместье Голубицкое", "Golubitskoe Estate"]}])
    load_catalog_index.cache_clear()
    plain = load_catalog_index(str(csv_path), fields=("winery",))
    plain_rec_before, _ = plain.scores("Golubitskoe Estate")
    aliased = load_winery_index(str(csv_path), aliases_json=aliases_path)
    assert aliased is not plain
    # закэшированный "plain" не испорчен последующим вызовом load_winery_index()
    plain_rec_after, _ = plain.scores("Golubitskoe Estate")
    assert plain_rec_after == plain_rec_before
