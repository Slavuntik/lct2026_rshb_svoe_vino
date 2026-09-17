"""qa/tests/test_case_census.py — тесты чистой логики qa/case_census.py (agents/F3-census.md).

Только строковая/множественная логика — без PIL/cv2 (те функции лениво импортируют PIL
внутри себя и не вызываются отсюда), запускается под обычным qa/.venv:

    cd qa && .venv/bin/python -m pytest -q test_case_census.py   # (или из корня — см. Makefile)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import case_census as cc  # noqa: E402


# --------------------------------------------------------------------------------------
# Транслитерация — пары-свидетели РЕАЛЬНО найдены в датасете (см. reports/f3-case-census.md)
# --------------------------------------------------------------------------------------


class TestTransliterate:
    def test_confirmed_pairs(self):
        # (кириллица из CSV "Название фото", ожидаемый ключ файла из uploads/, БЕЗ хэша)
        pairs = [
            ("Спуманте белый брют", "spumante_belyj_bryut"),
            ("Агора Резерв Яхтинг Мерло", "agora_rezerv_yahting_merlo"),
            ("Агора Резерв Яхтинг Пино Гриджио", "agora_rezerv_yahting_pino_gridzhio"),
            ("Агора_Мускат Черный", "agora_muskat_chernyj"),
            ("Бастардо терруарное", "bastardo_terruarnoe"),
            ("Коллекция", "kollekcziya"),
            ("Ркацители", "rkacziteli"),
            ("Джарагъ", "dzharag"),
            ("Совиньон", "sovinon"),
            ("Блан де Нуар", "blan_de_nuar"),
        ]
        for cyr, expected_key in pairs:
            assert cc.normalize_key(cyr) == expected_key, cyr

    def test_soft_and_hard_sign_dropped(self):
        assert cc.CYRILLIC_TO_LATIN["ъ"] == ""
        assert cc.CYRILLIC_TO_LATIN["ь"] == ""
        assert cc.transliterate("объём") == "obyom"

    def test_non_cyrillic_passthrough_lowercased(self):
        assert cc.transliterate("DSC09173") == "dsc09173"
        assert cc.transliterate("fanagoriya-100.webp") == "fanagoriya-100.webp"

    def test_has_cyrillic(self):
        assert cc.has_cyrillic("Спуманте")
        assert not cc.has_cyrillic("Spumante")


class TestNormalizeKeyAndHash:
    def test_strip_single_hash_suffix(self):
        assert cc.strip_hash_suffix("fanagoriya_kaberne_ad8495191f") == "fanagoriya_kaberne"

    def test_strip_chained_hash_suffixes(self):
        stem = "Brut_d_Or_Riesling_704fdf2136_4e49dedcd5_428751153f"
        assert cc.strip_hash_suffix(stem) == "Brut_d_Or_Riesling"

    def test_strip_hash_suffix_noop_without_hash(self):
        assert cc.strip_hash_suffix("DSC09173") == "DSC09173"

    def test_normalize_key_collapses_punctuation(self):
        assert cc.normalize_key("Агора Резерв Яхтинг Совиньон — копия") == "agora_rezerv_yahting_sovinon_kopiya"

    def test_normalize_key_empty_for_pure_punctuation(self):
        assert cc.normalize_key("...") == ""


# --------------------------------------------------------------------------------------
# Индекс uploads/ и поиск кандидатов
# --------------------------------------------------------------------------------------


class TestUploadIndex:
    def test_previews_excluded(self):
        idx = cc.build_upload_index(["thumbnail_foo_ad8495191f.webp", "foo_ad8495191f.webp"])
        assert idx == {"foo": ["foo_ad8495191f.webp"]}

    def test_non_photo_ext_excluded(self):
        idx = cc.build_upload_index(["logo_ad8495191f.svg", "map_ad8495191f.geojson", "photo_ad8495191f.webp"])
        assert list(idx.keys()) == ["photo"]

    def test_exact_match_after_transliteration(self):
        idx = cc.build_upload_index(["Spumante_belyj_bryut_d34e854a7b.webp"])
        cands = cc.find_exact_candidates("Спуманте белый брют.webp", idx)
        assert cands == ["Spumante_belyj_bryut_d34e854a7b.webp"]

    def test_no_match_returns_empty(self):
        idx = cc.build_upload_index(["Spumante_belyj_bryut_d34e854a7b.webp"])
        assert cc.find_exact_candidates("PUSjv7dnMNilH25.webp", idx) == []

    def test_multi_candidate_exact(self):
        idx = cc.build_upload_index(["DSC09173_aaaaaaaaaa.webp", "DSC09173_bbbbbbbbbb.webp"])
        cands = cc.find_exact_candidates("DSC09173.webp", idx)
        assert set(cands) == {"DSC09173_aaaaaaaaaa.webp", "DSC09173_bbbbbbbbbb.webp"}


class TestSubstringFallback:
    def test_unique_substring_match(self):
        idx = cc.build_upload_index(["fanagoriya_100_ottenkov_krasnogo_kaberne_extra_suffix_ad8495191f.webp"])
        cands = cc.find_substring_candidates("fanagoriya-100-ottenkov-krasnogo-kaberne.webp", idx)
        assert len(cands) == 1

    def test_short_key_never_matches(self):
        idx = cc.build_upload_index(["ab_ad8495191f.webp"])
        assert cc.find_substring_candidates("ab.webp", idx) == []

    def test_ambiguous_multi_hit(self):
        idx = cc.build_upload_index(
            ["red_wine_bottle_photo_a_ad8495191f.webp", "red_wine_bottle_photo_b_bd8495191f.webp"]
        )
        cands = cc.find_substring_candidates("red_wine_bottle_photo.webp", idx)
        assert len(cands) == 2

    def test_rejects_generic_short_token_inside_long_unrelated_string(self):
        # Регрессия на реальный найденный баг: 'preview_166ff5f67a.webp' (ключ "preview",
        # 7 симв., проходил старый порог длины >=6) ложно совпадал по подстроке сразу с
        # 9 РАЗНЫМИ винами через их carve.photos-экспортные имена вида
        # '4285_eqF1Fau-no-bg-preview (carve.photos).webp' — "preview" там случайный общий
        # токен внутри строки в 5+ раз длиннее, не сигнал сходства.
        idx = cc.build_upload_index(["preview_166ff5f67a.webp"])
        cands = cc.find_substring_candidates("4285_eqF1Fau-no-bg-preview (carve.photos).webp", idx)
        assert cands == []


class TestClassifyUnmatched:
    def test_no_ref_when_no_lexical_overlap_anywhere(self):
        idx = cc.build_upload_index(["completely_unrelated_ad8495191f.webp"])
        assert cc.classify_unmatched("PUSjv7dnMNilH25TO9Bc.webp", idx) == "no_ref"

    def test_weak_overlap_when_word_fragment_present(self):
        idx = cc.build_upload_index(["aligote_barrel_reserve_ad8495191f.webp"])
        assert cc.classify_unmatched("aligote-completely-different-xyz.webp", idx) == "weak_lexical_overlap"


class TestResolveBestCandidate:
    def test_single_candidate_returned_as_is(self):
        assert cc.resolve_best_candidate(["only.webp"], lambda fn: None) == "only.webp"

    def test_picks_max_resolution(self):
        sizes = {"small.webp": (100, 100), "big.webp": (2000, 1500)}
        chosen = cc.resolve_best_candidate(["small.webp", "big.webp"], lambda fn: sizes.get(fn))
        assert chosen == "big.webp"

    def test_tie_break_by_name_when_no_size_info(self):
        chosen = cc.resolve_best_candidate(["b.webp", "a.webp"], lambda fn: None)
        assert chosen == "a.webp"


# --------------------------------------------------------------------------------------
# run_matcher — сквозной сценарий на синтетике, воспроизводящей реальные паттерны
# --------------------------------------------------------------------------------------


class TestRunMatcher:
    def _slug_table(self):
        return {
            "zb-vajn-spumante-bryut-beloe": {
                "name": "ЗБ вайн СПУМАНТЕ Брют белое", "winery": "Золотая Балка",
                "photo": "Спуманте белый брют.webp", "category": "Белое",
            },
            "aligote-barrel-2024": {
                "name": "Алиготе Баррель, 2024", "winery": "Коммуналка",
                "photo": "DSC09173.webp", "category": "Белое",
            },
            "aligote-barrel-2025": {
                "name": "Алиготе Баррель, 2025", "winery": "Коммуналка",
                "photo": "DSC09173.webp", "category": "Белое",
            },
            "orphan-slug": {
                "name": "Нечто", "winery": "Кто-то",
                "photo": "PUSjv7dnMNilH25TO9Bc.webp", "category": "Красное",
            },
        }

    def test_cyrillic_slug_matched(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp"]
        result = cc.run_matcher(self._slug_table(), uploads)
        entry = result["mapping"]["zb-vajn-spumante-bryut-beloe"]
        assert entry["chosen"] == "Spumante_belyj_bryut_d34e854a7b.webp"
        assert entry["match_method"] == "cyrillic_transliteration"

    def test_genuinely_absent_file_is_no_ref(self):
        # DSC09173.webp физически не существует нигде в uploads (как и в реальном датасете)
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp"]
        result = cc.run_matcher(self._slug_table(), uploads)
        assert "aligote-barrel-2024" in result["no_ref_slugs"]
        assert "aligote-barrel-2025" in result["no_ref_slugs"]
        assert "orphan-slug" in result["no_ref_slugs"]

    def test_shared_photo_family_when_file_exists(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp", "DSC09173_cccccccccc.webp"]
        result = cc.run_matcher(self._slug_table(), uploads)
        assert result["shared_files"]["DSC09173_cccccccccc.webp"] == [
            "aligote-barrel-2024",
            "aligote-barrel-2025",
        ]

    def test_multi_candidate_slug_tracked(self):
        uploads = ["DSC09173_aaaaaaaaaa.webp", "DSC09173_bbbbbbbbbb.webp"]
        table = {"aligote-barrel-2024": self._slug_table()["aligote-barrel-2024"]}
        result = cc.run_matcher(table, uploads)
        assert result["multi_candidate_slugs"] == ["aligote-barrel-2024"]
        assert result["mapping"]["aligote-barrel-2024"]["chosen"] in uploads

    # -- manual_matches (F4, agents/F4-data-hygiene.md, задача 2) --------------------

    def test_manual_override_applied_when_automatic_match_fails(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp", "some_other_photo_aaaaaaaaaa.webp"]
        result = cc.run_matcher(
            self._slug_table(), uploads, manual_matches={"orphan-slug": "some_other_photo_aaaaaaaaaa.webp"}
        )
        entry = result["mapping"]["orphan-slug"]
        assert entry["chosen"] == "some_other_photo_aaaaaaaaaa.webp"
        assert entry["match_method"] == "manual_override"
        assert "orphan-slug" not in result["no_ref_slugs"]
        assert result["manual_override_count"] == 1

    def test_manual_override_never_beats_a_successful_automatic_match(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp", "decoy_aaaaaaaaaa.webp"]
        result = cc.run_matcher(
            self._slug_table(), uploads, manual_matches={"zb-vajn-spumante-bryut-beloe": "decoy_aaaaaaaaaa.webp"}
        )
        entry = result["mapping"]["zb-vajn-spumante-bryut-beloe"]
        assert entry["chosen"] == "Spumante_belyj_bryut_d34e854a7b.webp"  # автоматический, не decoy
        assert entry["match_method"] == "cyrillic_transliteration"
        assert result["manual_override_count"] == 0

    def test_manual_override_ignores_stale_reference_to_missing_file(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp"]
        result = cc.run_matcher(
            self._slug_table(), uploads, manual_matches={"orphan-slug": "file_removed_since_curation.webp"}
        )
        assert result["mapping"]["orphan-slug"]["chosen"] is None
        assert "orphan-slug" in result["no_ref_slugs"]
        assert result["manual_override_count"] == 0

    def test_manual_override_unknown_slug_is_ignored_not_an_error(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp"]
        result = cc.run_matcher(
            self._slug_table(), uploads, manual_matches={"not-a-real-slug-in-this-table": "whatever.webp"}
        )
        assert result["manual_override_count"] == 0

    def test_no_manual_matches_behaves_exactly_like_before(self):
        uploads = ["Spumante_belyj_bryut_d34e854a7b.webp"]
        with_none = cc.run_matcher(self._slug_table(), uploads, manual_matches=None)
        without_arg = cc.run_matcher(self._slug_table(), uploads)
        assert with_none["mapping"] == without_arg["mapping"]
        assert with_none["manual_override_count"] == 0


class TestLoadManualMatches:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path):
        assert cc.load_manual_matches(tmp_path / "does-not-exist.yaml") == {}

    def test_parses_flat_yaml_with_comments_and_inline_comments(self, tmp_path: Path):
        p = tmp_path / "manual.yaml"
        p.write_text(
            "# header comment, ignored\n"
            "aligote-avtorskoe: 4285_eq_F1_Fau_6d7da0f321.webp  # Массандра — Алиготе Авторское\n"
            "kokur-avtorskoe: 4287_Y_Ch_P_Nz3_5f55816077.webp\n",
            encoding="utf-8",
        )
        assert cc.load_manual_matches(p) == {
            "aligote-avtorskoe": "4285_eq_F1_Fau_6d7da0f321.webp",
            "kokur-avtorskoe": "4287_Y_Ch_P_Nz3_5f55816077.webp",
        }

    def test_empty_file_returns_empty_dict(self, tmp_path: Path):
        p = tmp_path / "manual.yaml"
        p.write_text("# только комментарии, ни одной записи\n", encoding="utf-8")
        assert cc.load_manual_matches(p) == {}

    def test_non_mapping_yaml_raises(self, tmp_path: Path):
        p = tmp_path / "manual.yaml"
        p.write_text("- just\n- a\n- list\n", encoding="utf-8")
        import pytest

        with pytest.raises(ValueError):
            cc.load_manual_matches(p)

    def test_real_repo_file_parses_and_every_file_exists_on_disk(self):
        """Живой файл qa/manual_photo_matches.yaml — если vines/case-data доступен в этом
        окружении (не в git, может отсутствовать на CI), каждая запись должна указывать
        на реально существующий файл uploads/ — тот же контроль качества, что делался
        вручную при курировании словаря (см. reports/f4-data-hygiene.md)."""
        import pytest

        repo_file = Path(__file__).resolve().parent.parent / "manual_photo_matches.yaml"
        if not repo_file.is_file():
            pytest.skip("qa/manual_photo_matches.yaml отсутствует в этом окружении")
        uploads_dir = cc.DEFAULT_CASE_DATA_DIR / cc.UPLOADS_SUBPATH
        if not uploads_dir.is_dir():
            pytest.skip("case-data/ (вне git) недоступна в этом окружении")
        matches = cc.load_manual_matches(repo_file)
        assert len(matches) > 0
        missing = [(slug, fn) for slug, fn in matches.items() if not (uploads_dir / fn).is_file()]
        assert missing == []


# --------------------------------------------------------------------------------------
# Семьи near-dup
# --------------------------------------------------------------------------------------


class TestFamilies:
    def test_year_in_slug_family(self):
        base, year = cc.slug_year_and_base("aligote-barrel-2024")
        assert base == "aligote-barrel"
        assert year == "2024"

    def test_year_in_middle_of_slug(self):
        base, year = cc.slug_year_and_base("leto-kaberne-fran-rezerv-2020-suhoe-krasnoe")
        assert year == "2020"
        assert "2020" not in base.split("-")

    def test_no_year_in_slug(self):
        base, year = cc.slug_year_and_base("amelia")
        assert base == "amelia"
        assert year is None

    def test_levenshtein_basic(self):
        assert cc.levenshtein("abc", "abc") == 0
        assert cc.levenshtein("abc", "abd") == 1
        assert cc.levenshtein("", "abc") == 3

    def test_build_families_year_group(self):
        table = {
            "aligote-barrel-2024": {"name": "Алиготе Баррель, 2024", "winery": "W", "photo": "a.webp", "category": ""},
            "aligote-barrel-2025": {"name": "Алиготе Баррель, 2025", "winery": "W", "photo": "b.webp", "category": ""},
        }
        chosen = {"aligote-barrel-2024": "fileA.webp", "aligote-barrel-2025": "fileB.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 1
        fam = next(iter(families.values()))
        assert fam["slugs"] == ["aligote-barrel-2024", "aligote-barrel-2025"]
        assert fam["differentiator"] == "год-в-slug"

    def test_build_families_shared_file_overrides_year(self):
        # тот же год-паттерн, но ОБА слага физически делят один файл -> различитель
        # 'одинаковый-файл-неразличимы' важнее (см. classify_differentiator).
        table = {
            "aligote-barrel-2024": {"name": "Алиготе Баррель, 2024", "winery": "W", "photo": "a.webp", "category": ""},
            "aligote-barrel-2025": {"name": "Алиготе Баррель, 2025", "winery": "W", "photo": "a.webp", "category": ""},
        }
        chosen = {"aligote-barrel-2024": "same.webp", "aligote-barrel-2025": "same.webp"}
        families = cc.build_families(table, chosen)
        fam = next(iter(families.values()))
        assert fam["differentiator"] == "одинаковый-файл-неразличимы"

    def test_build_families_year_absent_for_one_member(self):
        table = {
            "david": {"name": "David", "winery": "W", "photo": "a.webp", "category": ""},
            "david-2021": {"name": "David 2021", "winery": "W", "photo": "b.webp", "category": ""},
        }
        chosen = {"david": "f1.webp", "david-2021": "f2.webp"}
        families = cc.build_families(table, chosen)
        fam = next(iter(families.values()))
        assert fam["differentiator"] == "год-в-этикетке-отсутствует"

    def test_build_families_category_differentiator(self):
        # "Вино Брют"/"Вино Полусухое" одной винодельни — «скелет» названия без
        # категорийного слова совпадает ("вино" == "вино") -> ровно случай case.md
        # ("одна серия, разные... категория ПРИ ОДИНАКОВОЙ ЭТИКЕТКЕ").
        table = {
            "wine-brut": {"name": "Вино Брют", "winery": "W", "photo": "a.webp", "category": "Брют"},
            "wine-semi-dry": {"name": "Вино Полусухое", "winery": "W", "photo": "b.webp", "category": "Полусухое"},
        }
        chosen = {"wine-brut": "f1.webp", "wine-semi-dry": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 1
        fam = next(iter(families.values()))
        assert fam["differentiator"] == "категория"

    def test_build_families_unrelated_names_no_family(self):
        table = {
            "wine-brut": {"name": "Совиньон Блан", "winery": "W", "photo": "a.webp", "category": "Белое"},
            "wine-semi-dry": {"name": "Каберне Фран", "winery": "W", "photo": "b.webp", "category": "Красное"},
        }
        chosen = {"wine-brut": "f1.webp", "wine-semi-dry": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 0  # разные slug-базы, разные фото, РАЗНЫЕ названия -> не семья

    def test_name_similarity_family_within_same_winery(self):
        table = {
            "wine-a": {"name": "Резерв Шардоне", "winery": "W", "photo": "a.webp", "category": "Белое"},
            "wine-b": {"name": "Резерв Шардоне ", "winery": "W", "photo": "b.webp", "category": "Белое"},
            "wine-c": {"name": "Совсем другое вино", "winery": "W", "photo": "c.webp", "category": "Красное"},
        }
        chosen = {"wine-a": "f1.webp", "wine-b": "f2.webp", "wine-c": "f3.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 1
        fam = next(iter(families.values()))
        assert set(fam["slugs"]) == {"wine-a", "wine-b"}

    def test_name_similarity_rejects_different_grape_variety_same_winery(self):
        # Регрессия на реальную ложную склейку: длинное общее название продуктовой линейки
        # + разный сорт винограда — НЕ near-dup (разные этикетки), старый процентный порог
        # (15% от длины) на длинных названиях это пропускал.
        table = {
            "kokur-wine": {
                "name": "Alma Valley Солнце, воздух, виноград Кокур белое полусладкое",
                "winery": "Alma Valley", "photo": "a.webp", "category": "",
            },
            "merlo-wine": {
                "name": "Alma Valley Солнце, воздух, виноград Мерло красное полусладкое",
                "winery": "Alma Valley", "photo": "b.webp", "category": "",
            },
        }
        chosen = {"kokur-wine": "f1.webp", "merlo-wine": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 0

    def test_name_similarity_skeleton_catches_category_only_difference(self):
        table = {
            "wine-brut": {"name": "Шардоне Резерв Брют", "winery": "W", "photo": "a.webp", "category": "Брют"},
            "wine-dry": {"name": "Шардоне Резерв Сухое", "winery": "W", "photo": "b.webp", "category": "Сухое"},
        }
        chosen = {"wine-brut": "f1.webp", "wine-dry": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 1
        fam = next(iter(families.values()))
        assert set(fam["slugs"]) == {"wine-brut", "wine-dry"}
        assert fam["differentiator"] == "категория"

    def test_name_similarity_rejects_short_codes_within_edit_distance(self):
        # Регрессия на реальную ложную склейку: винодельня Belmas называет вина
        # 2-буквенными кодами сорта ('Cf'=Каберне Фран, 'Pg'=Пино Гри и т.п.) — расстояние
        # Левенштейна между ЛЮБЫМИ двумя разными 2-буквенными кодами уже <=2, абсолютный
        # порог без учёта длины ложно объединял их.
        table = {
            "belmas-cf": {"name": "Cf", "winery": "Belmas", "photo": "a.webp", "category": ""},
            "belmas-pg": {"name": "Pg", "winery": "Belmas", "photo": "b.webp", "category": ""},
        }
        chosen = {"belmas-cf": "f1.webp", "belmas-pg": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 0

    def test_name_similarity_rejects_disjoint_grape_variety_numbered_line(self):
        # Регрессия на реальную ложную склейку: 'Blush #1'..'#4' одной винодельни AYA —
        # названия различаются на 1 цифру (прошли бы и скелет-, и дистанционную проверку),
        # но это четыре РАЗНЫХ сорта винограда -> разные этикетки, не near-dup.
        table = {
            "blush-1": {"name": "Blush #1", "winery": "AYA", "photo": "a.webp", "category": "", "grape": "Сира"},
            "blush-2": {"name": "Blush #2", "winery": "AYA", "photo": "b.webp", "category": "", "grape": "Мерло"},
        }
        chosen = {"blush-1": "f1.webp", "blush-2": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 0

    def test_name_similarity_keeps_overlapping_blend_grapes(self):
        # Купажи, разделяющие хотя бы один сорт, — НЕ конфликт, остаются кандидатом.
        table = {
            "blend-1": {
                "name": "Ди Каспико", "winery": "Derbent", "photo": "a.webp", "category": "",
                "grape": "Рислинг, Ркацители, Шардоне",
            },
            "blend-2": {
                "name": "Ди Каспико", "winery": "Derbent", "photo": "b.webp", "category": "",
                "grape": "Алиготе, Рислинг, Шардоне",
            },
        }
        chosen = {"blend-1": "f1.webp", "blend-2": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 1

    def test_name_similarity_scoped_to_same_winery(self):
        table = {
            "wine-a": {"name": "Шардоне", "winery": "Winery1", "photo": "a.webp", "category": ""},
            "wine-b": {"name": "Шардоне", "winery": "Winery2", "photo": "b.webp", "category": ""},
        }
        chosen = {"wine-a": "f1.webp", "wine-b": "f2.webp"}
        families = cc.build_families(table, chosen)
        assert len(families) == 0


# --------------------------------------------------------------------------------------
# Шумовая перепись
# --------------------------------------------------------------------------------------


class TestNoiseClassification:
    def test_svg_logo(self):
        assert cc.classify_noise_file("brand_ad8495191f.svg") == "svg_logo"

    def test_geojson_map(self):
        assert cc.classify_noise_file("regions_ad8495191f.geojson") == "geojson_map"

    def test_camera_original(self):
        assert cc.classify_noise_file("DSC09173_ad8495191f.webp") == "camera_original"
        assert cc.classify_noise_file("IMG_2186_ad8495191f.webp") == "camera_original"

    def test_random_s3_name(self):
        assert cc.classify_noise_file("fpZEJhYZPstBYLV9182XyzQ_ad8495191f.webp") == "random_s3_name"

    def test_other_photo_fallback(self):
        assert cc.classify_noise_file("some_descriptive_wine_photo_ad8495191f.webp") == "other_photo"

    def test_census_noise_counts(self):
        uploads = ["a_ad8495191f.webp", "b_ad8495191f.svg", "thumbnail_a_ad8495191f.webp"]
        noise = cc.census_noise(uploads, reference_files=set())
        assert noise["total_uploads"] == 3
        assert noise["total_previews"] == 1
        assert noise["total_originals"] == 2
        assert noise["total_noise"] == 2


class TestReferenceQuality:
    def test_percentiles_and_small_detection(self):
        chosen = {"s1": "a.webp", "s2": "b.webp", "s3": "c.webp"}
        sizes = {"a.webp": (300, 300), "b.webp": (1000, 1000), "c.webp": (2000, 2000)}
        q = cc.census_reference_quality(chosen, lambda fn: sizes[fn], small_threshold=400)
        assert q["n"] == 3
        assert q["min_short_side"] == 300
        assert q["small_count"] == 1

    def test_missing_size_goes_to_unreadable(self):
        chosen = {"s1": "missing.webp"}
        q = cc.census_reference_quality(chosen, lambda fn: None)
        assert q["unreadable_count"] == 1
        assert q["n"] == 0
