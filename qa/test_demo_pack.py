"""pytest для qa/demo_pack.py (агент F, agents/F-qa-demo.md).

Три группы тестов:
  * на РЕАЛЬНОМ каталоге vines (abrau-dyurso и alma-valley) — доказывают, что генератор
    реально работает на настоящих данных и универсален (не хардкод под одну винодельню);
  * на СИНТЕТИЧЕСКОЙ мини-фикстуре (qa/tests/fixtures/mini_catalog) — быстрые, детерминированные,
    в том числе намеренно сломанная карточка (пустое обязательное поле) с самым высоким
    рейтингом из всех — чтобы доказать, что фильтр обязательных полей отрабатывает ДО
    ранжирования, а не после;
  * unit-тесты на чистых функциях (is_nonempty, score_wine_doc) без файлового ввода-вывода.

Сетевые HEAD-запросы в этих тестах ВЫКЛЮЧЕНЫ (check_network=False) везде, кроме одного,
явно помеченного @pytest.mark.network — так основной прогон быстрый и не зависит от
доступности vino-svoe.ru, а живая проверка вежливого HEAD всё равно покрыта отдельно.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import demo_pack as dp
from conftest import fake_head_always_200


# ----------------------------------------------------------------------------------
# Реальный каталог vines — abrau-dyurso (первый пак по брифу) и alma-valley (второй,
# для проверки универсальности — выбрана винодельня с 39 винами, не Абрау).
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("winery_slug", ["abrau-dyurso", "alma-valley"])
def test_real_winery_pack_generates_and_is_valid(real_catalog_dir, real_ref_dir, winery_slug):
    pack = dp.build_pack(winery_slug, catalog_dir=real_catalog_dir, ref_dir=real_ref_dir, top_n=8)
    report = dp.validate_pack(pack, check_network=False, check_refusal_live=False)

    assert len(pack.bottles) == 8
    assert pack.winery["slug"] == winery_slug
    assert pack.winery["wine_count_in_catalog"] >= 8

    # Поля-минимум брифа непусты у каждой отобранной бутылки.
    for bottle in pack.bottles:
        assert dp.is_nonempty(bottle.name)
        assert dp.is_nonempty(bottle.color)
        assert dp.is_nonempty(bottle.sugar_category)
        assert dp.is_nonempty(bottle.grapes)
        assert dp.is_nonempty(bottle.food_pairings)
        assert dp.is_nonempty(bottle.description)
        assert dp.is_nonempty(bottle.image_url)
        assert bottle.source_url.startswith("https://")

    # Ранг возрастает и совпадает с порядком в списке (1..8 без дыр).
    assert [b.rank for b in pack.bottles] == list(range(1, 9))

    # Сцена 1 всегда доступна (пак непуст), опирается на бутылку #1.
    scene1 = pack.scenario["scene_1_scan"]
    assert scene1["expected_wine_id"] == pack.bottles[0].wine_id

    # Только errors учитываются в report.ok; сеть и живая refusal-проверка выключены явно,
    # так что единственные возможные предупреждения — про то, что они выключены (не ошибка).
    assert report.ok, [dp._issue_dict(i) for i in report.errors]
    assert all(
        i.code in ("network_check_skipped", "refusal_probe_live_check_disabled") for i in report.warnings
    ), [dp._issue_dict(i) for i in report.warnings]


def test_abrau_pack_has_three_grounded_scenes(real_catalog_dir, real_ref_dir):
    """Раздел 0 плана: скан -> вопрос сомелье по реальным гастропарам -> аналог импортного.
    На реальных данных Абрау все три сцены обязаны быть доступны (не "insufficient data")."""
    pack = dp.build_pack("abrau-dyurso", catalog_dir=real_catalog_dir, ref_dir=real_ref_dir, top_n=8)

    scene2 = pack.scenario["scene_2_sommelier"]
    assert scene2["available"] is True
    assert scene2["question"]  # непустой текст вопроса
    assert len(scene2["expected_citations"]) >= 1
    # Вопрос обязан быть про гастропару, реально встречающуюся у бутылки пака (не выдумка).
    assert any(scene2["based_on_real_pairing"] in b.food_pairings for b in pack.bottles)

    scene3 = pack.scenario["scene_3_analog"]
    assert scene3["available"] is True
    illustrative_bottle = next(b for b in pack.bottles if b.wine_id == scene3["illustrative_wine_id"])
    assert scene3["style_slug"] in illustrative_bottle.reference_style_matches
    # Достижимый инвариант (не байт-в-байт бутылка, см. build_scene_analog): у винодельни
    # ЕСТЬ хотя бы одно вино этого стиля по всему каталогу, не только среди топ-8 пака.
    assert scene3["winery_style_wine_count"] >= 1
    assert illustrative_bottle.wine_id in scene3["winery_style_wine_ids"]

    # Deep-link — страховка от плохого света (mvp-plan.html, раздел 0) — есть у каждой
    # бутылки пака и у сцены 1 отдельно (это одно и то же значение, для удобства чтения).
    for bottle in pack.bottles:
        assert bottle.deep_link == f"/app/wine/{bottle.wine_id}"
    scene1 = pack.scenario["scene_1_scan"]
    assert scene1["deep_link_fallback"] == pack.bottles[0].deep_link


def test_abrau_pack_refusal_probe_from_real_goldset(real_catalog_dir, real_ref_dir):
    """Ревью 03: «момент доверия» демо обязан использовать вопрос, ГАРАНТИРОВАННО отклоняемый
    — из проверенного голд-сета калибровки (packages/rag/eval/goldset.jsonl, type=refusal),
    не импровизацию генератора (импровизированное «какое вино снижает давление?» не проходит
    отсечку — там есть слово «вино»)."""
    pack = dp.build_pack("abrau-dyurso", catalog_dir=real_catalog_dir, ref_dir=real_ref_dir, top_n=8)

    assert pack.refusal_probe is not None, "packages/rag/eval/goldset.jsonl должен существовать в этом репо"
    question = pack.refusal_probe["question"]
    assert question
    assert "вино" not in question.lower(), "вопрос не должен содержать слово, резонирующее с отсечкой"
    assert pack.refusal_probe["total_refusal_examples_in_goldset"] >= 1

    # Вопрос реально взят из голд-сета, не придуман генератором.
    goldset_questions = {
        json.loads(line)["q"]
        for line in dp.DEFAULT_GOLDSET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("type") == "refusal"
    }
    assert question in goldset_questions


def test_pack_reproducible_across_three_runs(real_catalog_dir, real_ref_dir):
    """mvp-plan.html §3, DoD агента F: «пак Абрау-Дюрсо — 3 сухих прогона подряд». Одинаковый
    вход обязан давать одинаковый пак (кроме метки времени)."""
    packs = [
        dp.pack_to_json_dict(
            dp.build_pack("abrau-dyurso", catalog_dir=real_catalog_dir, ref_dir=real_ref_dir, top_n=8),
            dp.ValidationReport(),
        )
        for _ in range(3)
    ]
    for p in packs:
        p.pop("generated_at", None)
    assert packs[0] == packs[1] == packs[2]


@pytest.mark.network
def test_polite_head_against_live_portal_returns_200(real_catalog_dir, real_ref_dir):
    """Единственный тест сессии с настоящей сетью — ровно то, что бриф разрешает явно
    («Не звать внешние сервисы кроме вежливых HEAD на vino-svoe.ru»). Один пак, реальные
    паузы, короткий таймаут: если портал недоступен из песочницы — падаем понятной причиной,
    а не зависаем."""
    pack = dp.build_pack("abrau-dyurso", catalog_dir=real_catalog_dir, ref_dir=real_ref_dir, top_n=8)
    try:
        report = dp.validate_pack(
            pack, check_network=True, timeout=5.0, polite_delay=0.3, check_refusal_live=False
        )
    except Exception as exc:  # pragma: no cover — защита от полной недоступности сети в CI
        pytest.skip(f"сеть недоступна в этом окружении: {exc!r}")
    bad = [i for i in report.warnings if i.code in ("source_url_bad_status", "source_url_unreachable")]
    assert not bad, bad
    assert report.checked_urls >= 8


# ----------------------------------------------------------------------------------
# Синтетическая мини-фикстура: быстрые, детерминированные тесты + сломанная карточка.
# ----------------------------------------------------------------------------------


def test_broken_card_excluded_from_selection_despite_highest_rating(mini_catalog_dir, mini_ref_dir):
    """DoD брифа: «автопроверка ловит подсунутую сломанную карточку (фикстура с пустым
    полем)». test-winery-wine-09-broken имеет rating=4.95 (выше всех) и пустое description —
    если бы фильтр был мягким тай-брейком, а не жёстким условием, она попала бы в топ-8."""
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)

    selected_ids = {b.wine_id for b in pack.bottles}
    assert "test-winery-wine-09-broken" not in selected_ids
    assert len(pack.bottles) == 8  # все 8 годных карточек вошли, ни одной не потеряно зря

    excluded_ids = {item["wine_id"] for item in pack.selection["excluded_incomplete"]}
    assert excluded_ids == {"test-winery-wine-09-broken"}
    broken_record = pack.selection["excluded_incomplete"][0]
    assert "description" in broken_record["missing_fields"]

    report = dp.validate_pack(pack, check_network=False, check_refusal_live=False)
    assert report.ok, [dp._issue_dict(i) for i in report.errors]


def test_validate_pack_catches_slipped_in_broken_bottle(mini_catalog_dir, mini_ref_dir):
    """Защита в глубину: даже если сломанная карточка каким-то образом ОБОШЛА select_top_bottles
    и попала в pack.bottles напрямую, validate_pack обязана поймать её независимо — это
    буквально то, что «автопроверка ловит подсунутую сломанную карточку»."""
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)

    broken_bottle = dp.Bottle(
        rank=99,
        wine_id="slipped-in-broken",
        name="Подсунутое вино",
        winery_slug="test-winery",
        winery_name="Тестовая Винодельня",
        region_name="Тестовый регион",
        color="красное",
        sugar_category="сухое",
        grapes=["Каберне Совиньон"],
        food_pairings=["Сыры"],
        description="",  # <- сломанное поле
        image_url="https://example.invalid/img/x.webp",
        vintage=2022,
        public_rating=5.0,
        source_url="https://example.invalid/wines/slipped-in-broken",
        reference_style_matches=[],
        score=dp.BottleScore(rating=5.0, rating_component=1.0, completeness_component=0.5, composite=0.825),
        deep_link=dp.deep_link_for("slipped-in-broken"),
    )
    pack.bottles.append(broken_bottle)

    report = dp.validate_pack(pack, check_network=False, check_refusal_live=False)

    assert not report.ok
    matching = [i for i in report.errors if i.wine_id == "slipped-in-broken" and i.code == "empty_required_field"]
    assert matching, [dp._issue_dict(i) for i in report.errors]
    assert "description" in matching[0].message


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("name", ""),
        ("name", "   "),
        ("color", None),
        ("grapes", []),
        ("food_pairings", []),
        ("description", ""),
        ("image_url", ""),
    ],
)
def test_missing_minimum_fields_detects_every_required_field(field_name, bad_value):
    source = {
        "name": "X",
        "color": "белое",
        "sugar_category": "сухое",
        "grapes": ["Шардоне"],
        "food_pairings": ["Сыры"],
        "description": "Текст",
        "image_url": "https://example.invalid/x.webp",
    }
    source[field_name] = bad_value
    assert field_name in dp.missing_minimum_fields(source)


def test_missing_minimum_fields_empty_when_all_present():
    source = {
        "name": "X",
        "color": "белое",
        "sugar_category": "сухое",
        "grapes": ["Шардоне"],
        "food_pairings": ["Сыры"],
        "description": "Текст",
        "image_url": "https://example.invalid/x.webp",
    }
    assert dp.missing_minimum_fields(source) == []


def test_scene_grounding_on_synthetic_pack(mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)

    scene2 = pack.scenario["scene_2_sommelier"]
    assert scene2["available"] is True
    assert scene2["based_on_real_pairing"] == "Сыры"  # 5 из 8 синтетических вин — самая частая

    scene3 = pack.scenario["scene_3_analog"]
    assert scene3["available"] is True
    assert scene3["style_slug"] == "test-bordeaux-like"  # первая по рангу бутылка со стилем
    assert scene3["style_name"] == "Тестовый Бордо-стиль"
    assert scene3["user_line"] == "Люблю тестовый Бордо-стиль"
    assert scene3["winery_slug"] == "test-winery"
    # Только test-winery-wine-01 несёт "test-bordeaux-like" среди всех 9 синтетических карточек
    # (включая сломанную) — достижимый инвариант считается по ВСЕЙ винодельне, не только топ-8.
    assert scene3["winery_style_wine_count"] == 1
    assert scene3["winery_style_wine_ids"] == ["test-winery-wine-01"]


def test_pack_is_deterministic_on_synthetic_catalog(mini_catalog_dir, mini_ref_dir):
    ids_1 = [b.wine_id for b in dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8).bottles]
    ids_2 = [b.wine_id for b in dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8).bottles]
    assert ids_1 == ids_2


def test_write_pack_creates_readable_files(tmp_path, mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)
    report = dp.validate_pack(pack, check_network=False, check_refusal_live=False)

    json_path, md_path = dp.write_pack(pack, report, tmp_path / "test-winery")

    assert json_path.exists() and md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["winery"]["slug"] == "test-winery"
    assert len(data["bottles"]) == 8

    md_text = md_path.read_text(encoding="utf-8")
    assert "Тестовая Винодельня" in md_text
    assert "Сцена 1" in md_text and "Сцена 2" in md_text and "Сцена 3" in md_text
    assert "готов к показу" in md_text


# ----------------------------------------------------------------------------------
# Отбор и скоринг — чистые функции, без файлового ввода-вывода.
# ----------------------------------------------------------------------------------


def _doc(slug, rating=None, **overrides):
    source = {
        "name": slug,
        "winery": "w",
        "winery_name": "W",
        "region_name": "R",
        "color": "белое",
        "sugar_category": "сухое",
        "grapes": ["Шардоне"],
        "food_pairings": ["Сыры"],
        "description": "Текст",
        "image_url": "https://example.invalid/x.webp",
        "public_rating": rating,
    }
    source.update(overrides.pop("source_overrides", {}))
    doc = {"slug": slug, "source": source, "derived": {}, "provenance": {"source_url": f"https://example.invalid/{slug}"}}
    doc.update(overrides)
    return doc


def test_select_top_bottles_orders_by_rating_first():
    docs = [_doc("low", rating=3.0), _doc("high", rating=4.9), _doc("mid", rating=4.0)]
    bottles, excluded = dp.select_top_bottles(docs, top_n=8)
    assert [b.wine_id for b in bottles] == ["high", "mid", "low"]
    assert excluded == []


def test_select_top_bottles_ties_broken_alphabetically_by_name():
    # Одинаковый рейтинг и одинаковая полнота -> одинаковый composite -> тай-брейк по имени.
    docs = [_doc("z-wine", rating=4.5, source_overrides={"name": "Яблочное"}),
            _doc("a-wine", rating=4.5, source_overrides={"name": "Абрикосовое"})]
    bottles, _ = dp.select_top_bottles(docs, top_n=8)
    assert [b.name for b in bottles] == ["Абрикосовое", "Яблочное"]


def test_select_top_bottles_returns_fewer_when_not_enough_valid_wines():
    docs = [_doc("only-one", rating=4.0)]
    bottles, excluded = dp.select_top_bottles(docs, top_n=8)
    assert len(bottles) == 1
    assert excluded == []


def test_select_top_bottles_excludes_incomplete_regardless_of_rating():
    docs = [_doc("broken", rating=5.0, source_overrides={"description": ""}), _doc("ok", rating=1.0)]
    bottles, excluded = dp.select_top_bottles(docs, top_n=8)
    assert [b.wine_id for b in bottles] == ["ok"]
    assert excluded[0]["wine_id"] == "broken"
    assert "description" in excluded[0]["missing_fields"]


def test_score_wine_doc_without_rating_uses_only_completeness():
    score, missing = dp.score_wine_doc(_doc("no-rating", rating=None))
    assert missing == []
    assert score.rating is None
    assert score.rating_component == 0.0
    assert score.composite == pytest.approx(dp.COMPLETENESS_WEIGHT * score.completeness_component)


def test_score_wine_doc_full_rating_and_full_optional_signals_is_composite_one():
    doc = _doc(
        "perfect",
        rating=5.0,
        source_overrides={
            "vintage": 2022,
            "color_in_glass": "рубиновый",
            "abv_percent": 13.0,
            "similar_wine_slugs": ["x"],
        },
        derived={"reference_style_matches": ["some-style"]},
    )
    score, missing = dp.score_wine_doc(doc)
    assert missing == []
    assert score.composite == pytest.approx(1.0)


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0 вин"),
        (1, "1 вино"),
        (2, "2 вина"),
        (3, "3 вина"),
        (4, "4 вина"),
        (5, "5 вин"),
        (11, "11 вин"),
        (12, "12 вин"),
        (14, "14 вин"),
        (21, "21 вино"),
        (22, "22 вина"),
        (25, "25 вин"),
        (101, "101 вино"),
        (114, "114 вин"),
    ],
)
def test_ru_wine_count_agreement(n, expected):
    assert dp._ru_wine_count(n) == expected


# ----------------------------------------------------------------------------------
# Ошибки использования — понятное сообщение, не трейсбек.
# ----------------------------------------------------------------------------------


def test_unknown_winery_raises_clear_error(real_catalog_dir, real_ref_dir):
    with pytest.raises(dp.DemoPackError, match="не найдена"):
        dp.build_pack("no-such-winery-xyz", catalog_dir=real_catalog_dir, ref_dir=real_ref_dir)


def test_winery_with_zero_wines_raises_clear_error(tmp_path):
    catalog_dir = tmp_path / "catalog"
    (catalog_dir / "wineries").mkdir(parents=True)
    (catalog_dir / "wines").mkdir(parents=True)
    (catalog_dir / "wineries" / "empty-winery.json").write_text(
        json.dumps({"slug": "empty-winery", "source": {"name": "Пустая"}, "provenance": {}}), encoding="utf-8"
    )
    with pytest.raises(dp.DemoPackError, match="нет ни одного вина"):
        dp.build_pack("empty-winery", catalog_dir=catalog_dir, ref_dir=tmp_path / "ref")


def test_winery_with_only_broken_wines_raises_clear_error(tmp_path):
    catalog_dir = tmp_path / "catalog"
    (catalog_dir / "wineries").mkdir(parents=True)
    (catalog_dir / "wines").mkdir(parents=True)
    (catalog_dir / "wineries" / "w.json").write_text(
        json.dumps({"slug": "w", "source": {"name": "W"}, "provenance": {}}), encoding="utf-8"
    )
    broken = _doc("only-broken", rating=5.0, source_overrides={"description": ""})
    (catalog_dir / "wines" / "only-broken.json").write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(dp.DemoPackError, match="не прошло минимальный набор полей"):
        dp.build_pack("w", catalog_dir=catalog_dir, ref_dir=tmp_path / "ref")


# ----------------------------------------------------------------------------------
# Автопроверка source_url — код пути HEAD-запросов с инъекцией head_fn (без реальной сети).
# ----------------------------------------------------------------------------------


def test_validate_pack_network_check_uses_injected_head_fn_and_dedupes_urls(mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)
    calls: list[str] = []

    def counting_head(url: str, timeout: float) -> int:
        calls.append(url)
        return 200

    report = dp.validate_pack(
        pack, check_network=True, head_fn=counting_head, polite_delay=0, sleep_fn=lambda _s: None,
        check_refusal_live=False,
    )
    assert report.ok
    assert len(calls) == len(set(calls))  # ни одного URL не запросили дважды
    assert report.checked_urls == len(calls)


def test_validate_pack_flags_non_200_as_warning_not_error(mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)

    def head_404(url: str, timeout: float) -> int:
        return 404

    report = dp.validate_pack(
        pack, check_network=True, head_fn=head_404, polite_delay=0, sleep_fn=lambda _s: None,
        check_refusal_live=False,
    )
    assert report.ok  # 404 -> warning, не роняет пак
    assert any(i.code == "source_url_bad_status" for i in report.warnings)


def test_validate_pack_flags_network_exception_as_warning(mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)

    def head_boom(url: str, timeout: float) -> int:
        raise TimeoutError("simulated network timeout")

    report = dp.validate_pack(
        pack, check_network=True, head_fn=head_boom, polite_delay=0, sleep_fn=lambda _s: None,
        check_refusal_live=False,
    )
    assert report.ok
    assert any(i.code == "source_url_unreachable" for i in report.warnings)


def test_validate_pack_sleeps_politely_between_requests(mini_catalog_dir, mini_ref_dir):
    pack = dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)
    sleeps: list[float] = []
    dp.validate_pack(
        pack, check_network=True, head_fn=fake_head_always_200, polite_delay=0.42, sleep_fn=sleeps.append,
        check_refusal_live=False,
    )
    assert sleeps  # хотя бы одна пауза между уникальными ссылками
    assert all(s == 0.42 for s in sleeps)


# ----------------------------------------------------------------------------------
# refusal_probe и deep_link — ревью 03: «момент доверия» из проверенного голд-сета,
# автопроверка живым API; deep-link'и /app/wine/<slug> как страховка от плохого света.
# ----------------------------------------------------------------------------------


def _write_goldset(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "goldset.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
    return path


def test_deep_link_for_matches_client_route():
    assert dp.deep_link_for("abrau-dyurso-victor-dravigny-bryut") == "/app/wine/abrau-dyurso-victor-dravigny-bryut"


def test_load_refusal_probe_prefers_known_demo_friendly_question(tmp_path):
    goldset = _write_goldset(
        tmp_path,
        [
            # "wifi роутер" тоже в _REFUSAL_PROBE_PREFERENCE, но ПОЗЖЕ "сериала" в списке —
            # порядок предпочтения должен победить порядок в файле (сериал в файле не первый).
            {"q": "Как настроить wifi роутер дома?", "type": "refusal"},
            {"q": "Порекомендуй интересный сериал на выходные.", "type": "refusal"},
            {"q": "Какая столица Франции?", "type": "refusal"},
            {"q": "Что взять к стейку?", "type": "pairing"},  # не refusal — должен игнорироваться
        ],
    )
    probe = dp.load_refusal_probe(goldset)
    assert probe is not None
    assert probe["question"] == "Порекомендуй интересный сериал на выходные."
    assert probe["total_refusal_examples_in_goldset"] == 3


def test_load_refusal_probe_falls_back_to_sorted_first_when_no_preferred_match(tmp_path):
    goldset = _write_goldset(
        tmp_path,
        [
            {"q": "Как настроить wifi роутер дома?", "type": "refusal"},
            {"q": "Как починить карбюратор на старой машине?", "type": "refusal"},
        ],
    )
    probe = dp.load_refusal_probe(goldset)
    assert probe is not None
    # Ни один вопрос не входит в _REFUSAL_PROBE_PREFERENCE -> берём отсортированный первый
    # (детерминированность важнее произвольного порядка в файле).
    assert probe["question"] == sorted(
        ["Как настроить wifi роутер дома?", "Как починить карбюратор на старой машине?"]
    )[0]


def test_load_refusal_probe_returns_none_when_goldset_missing(tmp_path):
    assert dp.load_refusal_probe(tmp_path / "does-not-exist.jsonl") is None


def test_load_refusal_probe_returns_none_when_no_refusal_type_entries(tmp_path):
    goldset = _write_goldset(tmp_path, [{"q": "Что взять к стейку?", "type": "pairing"}])
    assert dp.load_refusal_probe(goldset) is None


def test_build_pack_refusal_probe_unavailable_when_goldset_missing(mini_catalog_dir, mini_ref_dir, tmp_path):
    pack = dp.build_pack(
        "test-winery",
        catalog_dir=mini_catalog_dir,
        ref_dir=mini_ref_dir,
        top_n=8,
        goldset_path=tmp_path / "does-not-exist.jsonl",
    )
    assert pack.refusal_probe is None

    report = dp.validate_pack(pack, check_network=False, check_refusal_live=False)
    assert report.ok  # отсутствие голд-сета — не ошибка пака, а честный warning
    assert any(i.code == "refusal_probe_unavailable" for i in report.warnings)


def _pack_with_refusal_probe(mini_catalog_dir, mini_ref_dir):
    return dp.build_pack("test-winery", catalog_dir=mini_catalog_dir, ref_dir=mini_ref_dir, top_n=8)


def test_validate_pack_refusal_probe_confirmed_refused_adds_no_issue(mini_catalog_dir, mini_ref_dir):
    pack = _pack_with_refusal_probe(mini_catalog_dir, mini_ref_dir)
    assert pack.refusal_probe is not None  # реальный голд-сет по умолчанию

    def fake_probe(question: str, api_url: str, timeout: float) -> dict:
        return {"status": "confirmed_refused", "detail": "ok"}

    report = dp.validate_pack(
        pack, check_network=False, check_refusal_live=True, refusal_probe_fn=fake_probe
    )
    assert report.ok
    assert not any(i.code.startswith("refusal_probe") for i in report.errors)
    assert not any(i.code.startswith("refusal_probe") for i in report.warnings)


def test_validate_pack_refusal_probe_not_refused_is_error(mini_catalog_dir, mini_ref_dir):
    pack = _pack_with_refusal_probe(mini_catalog_dir, mini_ref_dir)

    def fake_probe(question: str, api_url: str, timeout: float) -> dict:
        return {"status": "confirmed_not_refused", "detail": "живой API ответил цитатой вместо отказа"}

    report = dp.validate_pack(
        pack, check_network=False, check_refusal_live=True, refusal_probe_fn=fake_probe
    )
    assert not report.ok
    assert any(i.code == "refusal_probe_not_refused" for i in report.errors)


def test_validate_pack_refusal_probe_unreachable_is_warning_not_error(mini_catalog_dir, mini_ref_dir):
    pack = _pack_with_refusal_probe(mini_catalog_dir, mini_ref_dir)

    def fake_probe(question: str, api_url: str, timeout: float) -> dict:
        return {"status": "unreachable", "detail": "localhost:8000: connection refused"}

    report = dp.validate_pack(
        pack, check_network=False, check_refusal_live=True, refusal_probe_fn=fake_probe
    )
    assert report.ok
    assert any(i.code == "refusal_probe_live_check_skipped" for i in report.warnings)


def test_validate_pack_refusal_probe_live_check_disabled_flag(mini_catalog_dir, mini_ref_dir):
    pack = _pack_with_refusal_probe(mini_catalog_dir, mini_ref_dir)

    def boom(*args, **kwargs):
        raise AssertionError("refusal_probe_fn не должен вызываться при check_refusal_live=False")

    report = dp.validate_pack(
        pack, check_network=False, check_refusal_live=False, refusal_probe_fn=boom
    )
    assert report.ok
    assert any(i.code == "refusal_probe_live_check_disabled" for i in report.warnings)


# ----------------------------------------------------------------------------------
# CLI целиком — то, что реально запускает `make demo-pack`.
# ----------------------------------------------------------------------------------


def test_cli_run_writes_files_and_returns_zero_on_valid_pack(tmp_path, mini_catalog_dir, mini_ref_dir):
    exit_code = dp.run(
        [
            "--winery", "test-winery",
            "--catalog-dir", str(mini_catalog_dir),
            "--ref-dir", str(mini_ref_dir),
            "--out-dir", str(tmp_path),
            "--no-network",
            "--no-refusal-check",
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "test-winery" / "pack.md").exists()
    assert (tmp_path / "test-winery" / "pack.json").exists()


def test_cli_run_returns_nonzero_on_unknown_winery(tmp_path, mini_catalog_dir, mini_ref_dir):
    exit_code = dp.run(
        [
            "--winery", "does-not-exist",
            "--catalog-dir", str(mini_catalog_dir),
            "--ref-dir", str(mini_ref_dir),
            "--out-dir", str(tmp_path),
            "--no-network",
        ]
    )
    assert exit_code == 2


def test_cli_subprocess_matches_makefile_invocation(tmp_path):
    """То же самое, что делает `make demo-pack WINERY=abrau-dyurso`, но как настоящий
    подпроцесс (не in-process вызов) — проверяет реальный entrypoint файла."""
    script = Path(__file__).resolve().parent / "demo_pack.py"
    result = subprocess.run(
        [sys.executable, str(script), "--winery", "abrau-dyurso", "--out-dir", str(tmp_path), "--no-network"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "abrau-dyurso" / "pack.md").exists()
    assert (tmp_path / "abrau-dyurso" / "pack.json").exists()
    assert "Абрау-Дюрсо" in result.stdout
