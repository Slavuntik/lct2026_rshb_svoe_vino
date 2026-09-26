"""Тесты LabelVerifier — contracts/image-scan.md v0.4.4, ревью 04 блокер 1.

Разделены на:
- юниты на `match_candidates()` (чистая функция, без OCR-движка — быстрые, много кейсов);
- юниты на регэкспы извлечения токенов;
- интеграционные тесты на реальном `LabelVerifier` (настоящий PaddleOCR) — DoD брифа:
  near-dup пара aligote-2024/2025 различается по году на читаемом синтетическом
  ракурсе, воздержание на нечитаемом, битые байты -> ValueError, бюджет p95.
"""
from __future__ import annotations

import json
import os

import cv2
import numpy as np
import pytest

from cv.verify import (
    LabelVerifier,
    VerifyCandidate,
    _extract_categories,
    _extract_color,
    _extract_volumes,
    _extract_wine_type,
    _extract_years,
    match_candidates,
    match_candidates_trace,
)

# --- match_candidates(): чистая функция, без OCR -----------------------------------


def _cand(slug: str, name: str = "Алиготе Баррель", vintage: int | None = None) -> VerifyCandidate:
    return {"slug": slug, "name": name, "vintage": vintage}


def test_match_distinguishes_by_vintage_year():
    candidates = [_cand("aligote-barrel-2024", vintage=2024), _cand("aligote-barrel-2025", vintage=2025)]
    assert match_candidates("АЛИГОТЕ БАРРЕЛЬ 2024 СУХОЕ 0.75Л", candidates) == "aligote-barrel-2024"
    assert match_candidates("текст 2025 на этикетке", candidates) == "aligote-barrel-2025"


def test_match_abstains_when_no_year_in_ocr_text():
    candidates = [_cand("a", vintage=2024), _cand("b", vintage=2025)]
    assert match_candidates("нечитаемый мусор без года", candidates) is None


def test_match_abstains_on_empty_ocr_text():
    candidates = [_cand("a", vintage=2024), _cand("b", vintage=2025)]
    assert match_candidates("", candidates) is None


def test_match_abstains_when_year_matches_neither_candidate():
    candidates = [_cand("a", vintage=2024), _cand("b", vintage=2025)]
    assert match_candidates("вино урожая 2019 года", candidates) is None


def test_match_abstains_with_empty_candidates_list():
    assert match_candidates("2024 сухое", []) is None


def test_match_abstains_when_year_ambiguous_between_candidates_without_vintage():
    """Оба кандидата несут один и тот же год В NAME (не в vintage) -> одинаковый
    скор -> воздержание, а не случайный выбор первого по порядку."""
    candidates = [_cand("a", name="Вино 2024"), _cand("b", name="Вино 2024, резерв")]
    # у "b" есть ещё категория "резерв", которая при наличии в OCR даст ему перевес —
    # проверяем именно случай, когда сигналы равны
    assert match_candidates("Вино 2024", candidates) is None


def test_match_uses_category_as_tiebreak_signal():
    candidates = [
        _cand("a", name="Вино брют", vintage=2024),
        _cand("b", name="Вино сухое", vintage=2024),
    ]
    # оба совпадают по году (одинаковый vintage) -> категория решает
    assert match_candidates("2024 БРЮТ", candidates) == "a"
    assert match_candidates("2024 СУХОЕ", candidates) == "b"


def test_match_single_candidate_with_any_signal_wins():
    candidates = [_cand("only", vintage=2024)]
    assert match_candidates("что-то 2024 что-то", candidates) == "only"


def test_match_prefers_vintage_field_over_name_year_when_conflicting():
    """vintage — структурное поле каталога, надёжнее текста name (мог устареть/
    не обновиться) — при конфликте побеждает vintage."""
    candidates = [_cand("a", name="Вино 1999 винтаж", vintage=2024)]
    assert match_candidates("на этикетке видно 2024", candidates) == "a"


# --- регэкспы извлечения токенов ----------------------------------------------------


def test_extract_years_finds_4digit_19xx_20xx():
    assert _extract_years("урожай 2024 года, до 1999 не продавалось") == {2024, 1999}


def test_extract_years_ignores_non_year_numbers():
    assert _extract_years("объём 750 мл, артикул 12345") == set()


def test_extract_years_ignores_year_embedded_in_longer_digit_run():
    assert _extract_years("серийный номер 320240001") == set()


def test_extract_volumes_recognizes_common_forms():
    assert len(_extract_volumes("0.75")) == 1
    assert len(_extract_volumes("0,75л")) == 1
    assert len(_extract_volumes("750 мл")) == 1
    assert len(_extract_volumes("1.5L")) == 1
    assert _extract_volumes("артикул 12345") == set()


def test_extract_categories_matches_ru_and_latin_keywords():
    assert _extract_categories("сухое вино") == {"сухое"}
    assert _extract_categories("brut reserve") == {"брют", "резерв"}
    assert _extract_categories("ничего релевантного") == set()


# --- тип вина / цвет (TODO-2, q2 — reports/g4-family-gap.md) ------------------------


def test_extract_wine_type_distinguishes_muskat_from_muskatel():
    """Найдено на q2: "мускат" — префикс строки символов "мускатель", но это ДВА
    разных типа вина на реальных этикетках Массандры — граница слова (\\b) обязана
    их различать, а не считать "мускат" совпавшим внутри "мускатель"."""
    assert _extract_wine_type("мускатель белый") == {"мускатель"}
    assert _extract_wine_type("мускат белый южнобережный") == {"мускат"}
    assert _extract_wine_type("портвейн белый гурзуф") == {"портвейн"}
    assert _extract_wine_type("совиньон блан") == set()


def test_extract_color_recognizes_ru_forms():
    assert _extract_color("мускатель белый") == {"белый"}
    assert _extract_color("мускатель черный") == {"черный"}
    assert _extract_color("вино белое") == {"белый"}
    assert _extract_color("ничего релевантного") == set()


def test_match_distinguishes_massandra_family_by_type_and_color():
    """Точное воспроизведение q2 (Мускатель Массандра Белый, reports/g4-family-gap.md):
    четыре near-dup соседа Массандры, OCR-текст — РЕАЛЬНЫЙ, снятый с
    case-data/eval/queries/02eef911.webp прогоном LabelVerifier.read_text() (записан
    в отчёте дословно). До правки категорий-типа/цвета ни один кандидат не получал
    сигнала вообще (все 0 -> None). После — "мускатель"+"белый" совпадают ТОЛЬКО с
    целевым слагом (2 очка), у остальных максимум 1 (тип ИЛИ цвет, не оба)."""
    ocr_text = "25.09.25 1894 МУСКАТЕЛЬ MYC MACCAHAPA KPA БЕЛЫЙ ГОДУРОЖАЯ 2023"
    candidates: list[VerifyCandidate] = [
        _cand("massandra-portveyn-belyy-gurzuf-kokur-belyy-beloe-sladkoe-135", name="Портвейн Белый Гурзуф"),
        _cand("massandra-muskat-belyy-yuzhnoberezhnyy-beloe-sladkoe-16", name="Мускат Белый Южнобережный"),
        _cand("massandra-muskatel-chernyy-krasnye-sorta-vinograda-krasnoe-sladkoe-16", name="Мускатель черный"),
        _cand("massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16", name="Мускатель белый"),
    ]
    assert match_candidates(ocr_text, candidates) == "massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16"


def test_match_candidates_trace_matches_match_candidates_decision():
    """match_candidates_trace() делит _score_candidate() с match_candidates() —
    решение ОБЯЗАНО совпадать, трассировка — не отдельная копия логики."""
    ocr_text = "25.09.25 1894 МУСКАТЕЛЬ MYC MACCAHAPA KPA БЕЛЫЙ ГОДУРОЖАЯ 2023"
    candidates: list[VerifyCandidate] = [
        _cand("massandra-portveyn-belyy-gurzuf-kokur-belyy-beloe-sladkoe-135", name="Портвейн Белый Гурзуф"),
        _cand("massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16", name="Мускатель белый"),
    ]
    decision, trace = match_candidates_trace(ocr_text, candidates)
    assert decision == match_candidates(ocr_text, candidates)
    assert trace["reason"] == "matched"
    assert trace["ocr_tokens"]["wine_types"] == ["мускатель"]
    assert trace["ocr_tokens"]["colors"] == ["белый"]
    assert trace["ocr_tokens"]["years"] == [2023]
    assert len(trace["per_candidate"]) == 2


def test_match_candidates_trace_reports_reason_for_each_abstention_kind():
    assert match_candidates_trace("2024 сухое", [])[1]["reason"] == "no_candidates"
    assert match_candidates_trace("нечитаемый мусор без сигнала", [_cand("a", vintage=2024)])[1]["reason"] == (
        "ocr_no_recognizable_tokens"
    )
    assert match_candidates_trace("вино урожая 2019 года", [_cand("a", vintage=2024), _cand("b", vintage=2025)])[1][
        "reason"
    ] == "no_candidate_scored"
    tied = [_cand("a", name="Вино 2024"), _cand("b", name="Вино 2024, резерв")]
    reason = match_candidates_trace("Вино 2024", tied)[1]["reason"]
    assert reason == "ambiguous_tie"


# --- CV_VERIFY_DEBUG (TODO-2, ревью 05) — трассировка verify() ----------------------


def test_verify_debug_off_by_default_no_trace_log(monkeypatch, capsys, synthetic_bottle_image):
    from cv.imageio import encode_jpeg

    monkeypatch.delenv("CV_VERIFY_DEBUG", raising=False)
    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    v.verify(data, [])  # пустые кандидаты — не трогает OCR, самый дешёвый путь
    assert "[cv.verify]" not in capsys.readouterr().err


def test_verify_debug_logs_trace_on_empty_candidates_without_touching_ocr(monkeypatch, capsys, synthetic_bottle_image):
    """Флаг включён, но кандидатов нет — лог обязан появиться (вызов был), а OCR
    всё равно не трогается (та же гарантия, что и при выключенном флаге)."""
    from cv.imageio import encode_jpeg

    monkeypatch.setenv("CV_VERIFY_DEBUG", "1")
    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    result = v.verify(data, [])
    assert result is None
    assert v._ocr is None  # OCR-движок не тронут — как и при выключенном флаге

    err = capsys.readouterr().err
    assert "[cv.verify]" in err
    line = next(line for line in err.splitlines() if "[cv.verify]" in line)
    payload = json.loads(line.split("[cv.verify] ", 1)[1])
    assert payload == {
        "called": True, "candidates": [], "ocr_text": None, "reason": "no_candidates", "decision": None,
    }


def test_verify_debug_does_not_change_decision(monkeypatch, label_verifier):
    """Контракт брифа: 'НЕ менять поведение при выключенном флаге' — а при
    ВКЛЮЧЁННОМ решение обязано остаться ТЕМ ЖЕ, что и без него (трассировка —
    побочный эффект в stderr, не альтернативная ветка принятия решения)."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    monkeypatch.delenv("CV_VERIFY_DEBUG", raising=False)
    without_debug = label_verifier.verify(data, _ALIGOTE_CANDIDATES)
    monkeypatch.setenv("CV_VERIFY_DEBUG", "1")
    with_debug = label_verifier.verify(data, _ALIGOTE_CANDIDATES)
    assert without_debug == with_debug == "aligote-barrel-2024"


def test_verify_debug_trace_contains_ocr_text_and_candidates(monkeypatch, capsys, label_verifier):
    """Дословно то, что требует бриф: вызван ли, кандидаты (slug/name/vintage),
    что распознал OCR (сырые строки) — всё в одной JSON-строке лога."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    monkeypatch.setenv("CV_VERIFY_DEBUG", "1")
    result = label_verifier.verify(data, _ALIGOTE_CANDIDATES)

    err = capsys.readouterr().err
    line = next(line for line in err.splitlines() if "[cv.verify]" in line)
    payload = json.loads(line.split("[cv.verify] ", 1)[1])
    assert payload["called"] is True
    assert payload["decision"] == result == "aligote-barrel-2024"
    assert payload["reason"] == "matched"
    assert isinstance(payload["ocr_text"], str) and payload["ocr_text"]  # сырая строка OCR, не пусто
    assert {c["slug"] for c in payload["candidates"]} == {"aligote-barrel-2024", "aligote-barrel-2025"}
    assert all({"slug", "name", "vintage"} <= c.keys() for c in payload["candidates"])
    assert 2024 in payload["ocr_tokens"]["years"]


# --- LabelVerifier: конструктор ленивый, decode -> ValueError ------------------------


def test_label_verifier_construction_does_not_load_model():
    v = LabelVerifier()
    assert v._ocr is None  # ленивая загрузка — конструктор не трогает PaddleOCR/сеть


def test_verify_on_corrupt_bytes_raises_value_error():
    v = LabelVerifier()
    with pytest.raises(ValueError):
        v.verify(b"not an image, just garbage bytes 0123456789", [_cand("x", vintage=2024)])
    with pytest.raises(ValueError):
        v.verify(b"", [_cand("x", vintage=2024)])


def test_verify_returns_none_immediately_on_empty_candidates(synthetic_bottle_image):
    """Пустой список кандидатов -> None без обращения к OCR (нечего сопоставлять)."""
    from cv.imageio import encode_jpeg

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    assert v.verify(data, []) is None
    assert v._ocr is None  # пустой список кандидатов проверяется ДО обращения к OCR-движку


# --- v0.4.12 (agents/B9-text-rerank-integration.md): `ocr_text` — переиспользование
# текста, прочитанного один раз apps/api для cv.text_rerank, без повторного OCR ------
#
# Все тесты ниже, где `ocr_text` ПЕРЕДАН, намеренно используют ДЕШЁВОЕ синтетическое
# фото (`synthetic_bottle_image`) и свежий `LabelVerifier()` — реальный движок PaddleOCR
# в этих сценариях не должен грузиться ВООБЩЕ (см. `v._ocr is None`), поэтому его
# отсутствие не влияет на результат: сама суть параметра в том, что фото не читается.


def test_verify_with_ocr_text_never_touches_read_text(monkeypatch, synthetic_bottle_image):
    """Явный `ocr_text` -> `read_text()` (реальный OCR-проход) не вызывается вовсе —
    решение принимается ровно по переданной строке. `monkeypatch` на `read_text`
    бросает исключение при вызове, так что любой (даже случайный) вызов OCR уронит тест."""
    from cv.imageio import encode_jpeg

    def _boom(self, image_arr):
        raise AssertionError("read_text() не должен вызываться, когда ocr_text передан")

    monkeypatch.setattr(LabelVerifier, "read_text", _boom)

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    result = v.verify(data, _ALIGOTE_CANDIDATES, ocr_text="сухое 2024 0.75л")

    assert result == "aligote-barrel-2024"
    assert v._ocr is None  # движок ни разу не тронут — не только read_text() не звался


def test_verify_empty_string_ocr_text_is_provided_not_none(monkeypatch, synthetic_bottle_image):
    """`ocr_text=""` — ВАЛИДНОЕ значение "передано, но нечего сопоставлять" (отличается
    от `None` = "не передано, прочитай сам"): OCR не вызывается, решение — воздержание
    (как `match_candidates("", ...)`), а не попытка прочитать текст заново."""
    from cv.imageio import encode_jpeg

    def _boom(self, image_arr):
        raise AssertionError("read_text() не должен вызываться — ocr_text передан (пустой, но не None)")

    monkeypatch.setattr(LabelVerifier, "read_text", _boom)

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    result = v.verify(data, _ALIGOTE_CANDIDATES, ocr_text="")

    assert result is None
    assert v._ocr is None


def test_verify_without_ocr_text_argument_still_calls_read_text(monkeypatch, synthetic_bottle_image):
    """Бит-в-бит регресс на старое поведение: БЕЗ параметра (2-позиционный вызов, как
    до этой волны) `verify()` по-прежнему сам вызывает `read_text()` — ровно один раз.
    `read_text` замокан (не настоящий PaddleOCR) — тест быстрый, проверяет ФАКТ вызова
    и то, что его результат реально используется для решения, а не сам движок OCR."""
    from cv.imageio import encode_jpeg

    calls: list[np.ndarray] = []

    def _fake_read_text(self, image_arr):
        calls.append(image_arr)
        return "2024"

    monkeypatch.setattr(LabelVerifier, "read_text", _fake_read_text)

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    result = v.verify(data, _ALIGOTE_CANDIDATES)  # старая, 2-позиционная сигнатура

    assert result == "aligote-barrel-2024"
    assert len(calls) == 1, "read_text() обязан вызваться РОВНО один раз, когда ocr_text не передан"


def test_verify_ocr_text_none_default_matches_omitting_the_argument(monkeypatch, synthetic_bottle_image):
    """`ocr_text=None` явно передан -> тот же результат, что и при полном отсутствии
    аргумента (дефолт) — `None` не самостоятельная ветка, а именно значение по
    умолчанию (contracts/image-scan.md v0.4.12: "старое поведение без параметра —
    бит в бит")."""
    from cv.imageio import encode_jpeg

    monkeypatch.setattr(LabelVerifier, "read_text", lambda self, image_arr: "2025")

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    omitted = v.verify(data, _ALIGOTE_CANDIDATES)
    explicit_none = v.verify(data, _ALIGOTE_CANDIDATES, ocr_text=None)

    assert omitted == explicit_none == "aligote-barrel-2025"


def test_verify_on_corrupt_bytes_raises_value_error_even_with_ocr_text_given():
    """decode -> ValueError остаётся ПЕРВЫМ шагом независимо от `ocr_text` — битые
    байты не долетают до сопоставления, даже если текст уже на руках у вызывающего."""
    v = LabelVerifier()
    with pytest.raises(ValueError):
        v.verify(b"not an image, just garbage bytes 0123456789", _ALIGOTE_CANDIDATES, ocr_text="2024")


def test_verify_debug_trace_reports_provided_ocr_text_verbatim_without_ocr(
    monkeypatch, capsys, synthetic_bottle_image
):
    """CV_VERIFY_DEBUG=1 + `ocr_text` передан -> трассировка несёт РОВНО переданную
    строку (не заново прочитанную), и движок OCR по-прежнему не тронут."""
    from cv.imageio import encode_jpeg

    def _boom(self, image_arr):
        raise AssertionError("read_text() не должен вызываться в debug-режиме, когда ocr_text передан")

    monkeypatch.setattr(LabelVerifier, "read_text", _boom)
    monkeypatch.setenv("CV_VERIFY_DEBUG", "1")

    v = LabelVerifier()
    data = encode_jpeg(synthetic_bottle_image)
    result = v.verify(data, _ALIGOTE_CANDIDATES, ocr_text="сухое 2024 0.75л")

    assert result == "aligote-barrel-2024"
    assert v._ocr is None

    err = capsys.readouterr().err
    line = next(line for line in err.splitlines() if "[cv.verify]" in line)
    payload = json.loads(line.split("[cv.verify] ", 1)[1])
    assert payload["ocr_text"] == "сухое 2024 0.75л"
    assert payload["decision"] == "aligote-barrel-2024"


# --- Интеграция с реальным PaddleOCR: DoD брифа (near-dup год, воздержание, бюджет) --
#
# aligote-barrel-2024/2025 в devfix — буквально один и тот же файл фото (одна этикетка,
# разный vintage в каталоге) — само по себе не несёт печатного года крупным планом на
# исходном разрешении 258x630 (см. reports/g-report.md). Чтобы DoD-тест был
# воспроизводим и не зависел от того, поместится ли конкретный мелкий год на конкретном
# фото в OCR-разрешение, строим СВОЙ эталон той же формы, что и настоящий каталог
# (тёмное "стекло" + светлая этикетка в нижних 2/3 — конвенция `normalize.detect_label_
# region`), с крупным читаемым годом — и прогоняем его через НАСТОЯЩИЙ `cv.augment.
# render_synthetic_views()` (контракт брифа: "сгенерируй такой аугментатором"), не
# используем сырое изображение напрямую.


def _make_bottle_label(year: int, w: int = 300, h: int = 630) -> np.ndarray:
    img = np.full((h, w, 3), 25, dtype=np.uint8)  # тёмное "стекло"
    y0, y1 = int(h * 0.35), int(h * 0.92)
    img[y0:y1, int(w * 0.08) : int(w * 0.92)] = 245  # светлая этикетка, нижние ~2/3
    cv2.putText(img, "ALIGOTE", (int(w * 0.13), y0 + 70), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (10, 10, 10), 3)
    cv2.putText(img, "BARREL", (int(w * 0.13), y0 + 140), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (10, 10, 10), 3)
    cv2.putText(img, str(year), (int(w * 0.13), y0 + 240), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (10, 10, 10), 4)
    cv2.putText(img, "SUHOE 0.75L", (int(w * 0.13), y0 + 310), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (10, 10, 10), 2)
    return img


_ALIGOTE_CANDIDATES: list[VerifyCandidate] = [
    {"slug": "aligote-barrel-2024", "name": "Алиготе Баррель", "vintage": 2024},
    {"slug": "aligote-barrel-2025", "name": "Алиготе Баррель", "vintage": 2025},
]

# Найдено эмпирически (см. reports/g-report.md): seed=0 view0 из render_synthetic_views
# сохраняет "2024"/"2025" читаемым после полной normalize_query(); view1 — тот же seed,
# следующий по порядку ракурс — размывает/обрезает текст до нечитаемого ("0.75L" без
# года). Оба — детерминированные, дальнейшие прогоны дают побитово те же ракурсы.
_READABLE_VIEW_SEED = 0
_READABLE_VIEW_INDEX = 0
_UNREADABLE_VIEW_INDEX = 1


@pytest.fixture(scope="module")
def label_verifier() -> LabelVerifier:
    return LabelVerifier()


@pytest.mark.parametrize("year,other_year", [(2024, 2025), (2025, 2024)])
def test_verify_distinguishes_near_dup_pair_by_year_on_readable_view(label_verifier, year, other_year):
    """DoD (оркестратор): пара aligote-2024/2025 различается по цифре года на
    синтетическом ракурсе с читаемым годом, сгенерированном аугментатором."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg

    ref = _make_bottle_label(year)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    result = label_verifier.verify(data, _ALIGOTE_CANDIDATES)
    assert result == f"aligote-barrel-{year}"
    assert result != f"aligote-barrel-{other_year}"


def test_verify_abstains_on_unreadable_view(label_verifier):
    """DoD: воздержание (None) на нечитаемом ракурсе — норма, не ошибка. Тот же
    эталон и seed, что и читаемый тест выше — только СЛЕДУЮЩИЙ по порядку ракурс,
    у которого искажения (блюр/шум/угол) уже съели текст года."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_UNREADABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_UNREADABLE_VIEW_INDEX])

    assert label_verifier.verify(data, _ALIGOTE_CANDIDATES) is None


_RUN_BENCHMARKS = os.environ.get("RUN_CV_BENCHMARKS") == "1"
_BENCHMARK_SKIP_REASON = (
    "замер задержки verify() — скип по умолчанию, RUN_CV_BENCHMARKS=1 включает "
    "(reports/ml-eng-verify-latency-test.md: та же машина за 10 минут дала медиану "
    "то 456мс, то 703мс без единой правки кода — соседние процессы команды/системы, "
    "не regression; см. также reports/g7-text-fusion.md, reports/h1-cpu-path.md)"
)


@pytest.mark.benchmark
@pytest.mark.skipif(not _RUN_BENCHMARKS, reason=_BENCHMARK_SKIP_REASON)
def test_verify_p95_latency_budget(label_verifier):
    """Контракт (contracts/image-scan.md): бюджет verify() <= 700 мс p95, установившийся
    режим (холодная загрузка модели вынесена из замера — см. `warmup=True` в
    `benchmark()`). Замер на читаемом ракурсе (худший случай по объёму работы OCR —
    есть что распознавать; на нечитаемом/пустом OCR обычно быстрее, т.к. меньше
    текстовых боксов проходит порог уверенности).

    ## Устойчивая статистика (reports/ml-eng-verify-latency-test.md, 26.09)

    n=30, не 8 (разбор красного прогона — architect/qa-auto,
    reports/architect-post-merge-review.md: p50 456/p95 2174/max 2980мс при n=8):
    при n=8 95-й перцентиль численно вырождается почти в максимум выборки (индекс
    p95 у 8 точек — между 6-м и 7-м из 8, т.е. исключается в лучшем случае один
    самый БЫСТРЫЙ вызов, ни один медленный), поэтому ЕДИНСТВЕННЫЙ вызов,
    приторможенный конкуренцией за CPU на разделяемом Mac, валил тест целиком без
    регрессии в коде — p50 в том красном прогоне был ЗДОРОВЫЙ (456мс), испорчен был
    только хвост. Git-история `cv/verify.py` (H1/H2/B9) подтверждает: путь
    `verify()` без переданного `ocr_text` не менялся с 22.09 (за 4 дня до мержа
    Михаила, который вообще не трогал packages/cv); собственные замеры на этой же
    машине без искусственной нагрузки — медиана 456-459мс на >70 прогонах, что
    совпадает с базовым замером автора теста при введении (513мс, коммит 6a7e47a).
    При n=30 p95 (индекс между 27-м и 28-м из 30) честно исключает единственный
    выброс. `max_ms` — ОТДЕЛЬНЫЙ потолок "не зависло", не бюджет контракта, с
    честным запасом: исторический баг near-dup verify() без таймаута давал 14.6с
    (reports/ml-eng-scan-budget.md, "Находка: near-dup verify() тоже без бюджета"),
    на этом фоне 4с уверенно отличают пересадку CPU-планировщиком от настоящего
    зависания.

    ## Почему всё равно скип по умолчанию (RUN_CV_BENCHMARKS=1 включает)

    Прямое воспроизведение нагрузкой (9 CPU-процессов внахлёст, 10-ядерный Mac)
    даёт p50 920/p95 1142мс — ожидаемо, это уже не единичный выброс, а сдвиг ВСЕГО
    распределения, никакая статистика по выборке такое не разлечит от настоящей
    регрессии. Хуже: та же картина наблюдалась и БЕЗ искусственной нагрузки — через
    10 минут после первого чистого замера (456мс) этот же тест с n=30 дал p50
    703мс/p95 947мс сам по себе, только от соседних процессов (другие агенты
    команды + системные, `frauddefensed`/StorageManagement — обычный режим этой
    машины, ORCHESTRATION.md "тайминги на Mac шумные"). Статистика по одному
    прогону не спасает от сдвига ВСЕГО распределения — только запуск на простаивающей
    машине. Поэтому тест помечен `@pytest.mark.benchmark` и скипается по умолчанию
    (как `RUN_CV_INTEGRATION` в apps/api) — не "skip ради зелёного" (проверка не
    ослаблена, бюджет 700мс не тронут), а честное признание, что wall-clock тест не
    может быть частью детерминированного гейта на общей машине. Прогонять вручную
    на простаивающей машине: `RUN_CV_BENCHMARKS=1 pytest tests/test_verify.py -k
    p95_latency -v`."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg
    from cv.verify import benchmark

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    report = benchmark(label_verifier, [data], _ALIGOTE_CANDIDATES, n=30)
    assert report["p95_ms"] <= 700, f"verify() p95={report['p95_ms']}ms превышает бюджет 700мс: {report}"
    assert report["max_ms"] <= 4000, (
        f"verify() max={report['max_ms']}ms — похоже на зависание/деградацию, не на шум CPU: {report}"
    )


# --- v0.4.12 (agents/B9-text-rerank-integration.md): read_query_text() — реальный
# PaddleOCR, DoD "публичный метод чтения текста запроса, переиспользуемый ocr_text" ---


def test_read_query_text_equals_manual_normalize_and_read_text_pipeline(label_verifier):
    """`read_query_text()` — ровно decode -> normalize_query() -> read_text(), тот же
    кроп, что видит verify() изнутри без ocr_text. Сравниваем с РУЧНЫМ повторением тех
    же трёх шагов на том же фото — обязаны дать побитово одинаковую строку."""
    from cv import imageio
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg
    from cv.normalize import normalize_query

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    via_public_method = label_verifier.read_query_text(data)

    arr = imageio.decode_image(data)
    normalized = normalize_query(arr, enabled=True)
    via_manual_steps = label_verifier.read_text(normalized)

    assert via_public_method == via_manual_steps
    assert "2024" in via_public_method


def test_verify_ocr_text_overrides_what_the_photo_actually_shows(label_verifier):
    """Сильное доказательство "OCR не повторяется": фото реально несёт год 2025
    (читаемый ракурс) — не в этом кандидатском словаре. Если бы verify() читал OCR
    заново, он честно получил бы 2025 и воздержался (2025 не в списке кандидатов ниже
    — только 2024/2026). Передаём заведомо ДРУГОЙ текст (2026) явным `ocr_text` — решение
    обязано последовать за ПЕРЕДАННЫМ текстом, а не за тем, что реально на фото."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg

    ref = _make_bottle_label(2025)  # фото реально показывает 2025
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    # Санити: реальный OCR этого фото читает именно 2025, не 2026 — иначе тест
    # ничего не доказывает (могло бы случайно совпасть).
    real_text = label_verifier.read_query_text(data)
    assert "2025" in real_text
    assert "2026" not in real_text

    candidates: list[VerifyCandidate] = [
        {"slug": "aligote-barrel-2024", "name": "Алиготе Баррель", "vintage": 2024},
        {"slug": "aligote-barrel-2026", "name": "Алиготе Баррель", "vintage": 2026},
    ]
    result = label_verifier.verify(data, candidates, ocr_text="урожай 2026 сухое")

    assert result == "aligote-barrel-2026", "решение обязано следовать ЗА ПЕРЕДАННЫМ текстом, не за фото"


# --- agents/H1-cpu-path.md, задача 1: CV_OCR_QUERY_MODE/CV_OCR_CENTER_SIZE ----------
#
# Все тесты этого раздела — БЕЗ реального PaddleOCR (брифа п.4: "без сети, без
# PaddleOCR в юнитах"): `_StubOCR` подменяет `self._ocr` НАПРЯМУЮ (обходит ленивую
# `_load()`, у которой `if self._ocr is not None: return`), так что тестируется
# только кадрирование/маршрутизация — какой МАССИВ и с какой целью даунскейла
# доходит до движка, — а не сам PaddleOCR.


class _StubOCR:
    """Двойник PaddleOCR-движка: помнит форму каждого массива, который РЕАЛЬНО
    дошёл бы до `predict()`, ничего не распознаёт (пустой список кандидатов текста —
    `read_text()` тогда честно отдаёт "")."""

    def __init__(self):
        self.seen: list[np.ndarray] = []

    def predict(self, image_arr):
        self.seen.append(image_arr)
        return []


def _stubbed_verifier(**kwargs) -> tuple[LabelVerifier, _StubOCR]:
    v = LabelVerifier(**kwargs)
    stub = _StubOCR()
    v._ocr = stub  # пропускаем _load() целиком — модель никогда не тронута
    return v, stub


def test_default_ocr_query_mode_is_detector():
    """Дефолт брифа: 'дефолт пока detector, включим после приёмки' — молчаливой
    смены поведения быть не должно."""
    assert LabelVerifier().ocr_query_mode == "detector"


def test_default_center_size_is_640():
    assert LabelVerifier().center_size == 640


def test_ocr_query_mode_env_var_overrides_constructor_default(monkeypatch):
    monkeypatch.setenv("CV_OCR_QUERY_MODE", "center")
    assert LabelVerifier().ocr_query_mode == "center"


def test_ocr_query_mode_env_var_is_case_and_whitespace_normalized(monkeypatch):
    monkeypatch.setenv("CV_OCR_QUERY_MODE", " CENTER \n")
    assert LabelVerifier().ocr_query_mode == "center"


def test_ocr_center_size_env_var_overrides_constructor_default(monkeypatch):
    monkeypatch.setenv("CV_OCR_CENTER_SIZE", "800")
    assert LabelVerifier().center_size == 800


def test_center_crop_constant_pins_brief_fractions():
    """Те же доли, что apps/api/app/cv/vision_llm.py::CENTER_CROP (вход VLM) и
    qa/real_photos_features.py::CWIDE (офлайн-эксперимент) — намеренно один и тот
    же кроп во всех трёх путях чтения этикетки (agents/H1-cpu-path.md)."""
    from cv.verify import CENTER_CROP

    assert CENTER_CROP == (0.15, 0.05, 0.85, 0.98)


def test_center_crop_slices_full_frame_by_fixed_fractions():
    from cv.verify import _center_crop

    arr = np.arange(1000 * 2000 * 3, dtype=np.uint8).reshape(1000, 2000, 3)
    cropped = _center_crop(arr)
    assert cropped.shape == (930, 1400, 3)  # (0.98-0.05)*1000, (0.85-0.15)*2000
    assert np.array_equal(cropped, arr[50:980, 300:1700])


def test_read_text_size_param_overrides_ocr_size_downscale_target():
    """agents/H1-cpu-path.md: `size=` — независимая цель даунскейла от
    `self.ocr_size` (та используется только когда `size` не передан)."""
    v, stub = _stubbed_verifier(ocr_size=320)
    big = np.zeros((100, 2000, 3), dtype=np.uint8)

    v.read_text(big, size=640)

    assert stub.seen[0].shape == (32, 640, 3)  # 100*640/2000=32, широкая сторона -> 640


def test_read_text_without_size_param_keeps_using_ocr_size():
    """Регресс: старое поведение (без `size=`) не сдвинулось — цель даунскейла
    по-прежнему `self.ocr_size`, бит-в-бит как до этой правки."""
    v, stub = _stubbed_verifier(ocr_size=320)
    big = np.zeros((100, 2000, 3), dtype=np.uint8)

    v.read_text(big)

    assert max(stub.seen[0].shape[:2]) == 320


def test_read_query_text_center_feeds_full_frame_crop_not_detector_square():
    """`CV_OCR_QUERY_MODE=center`: массив, дошедший до движка, — кроп ПОЛНОГО
    кадра (930x1400 после CENTER_CROP), даунскейленный до `center_size` (640) —
    НЕ квадрат 320x320, который даёт режим "detector" (см. следующий тест)."""
    from cv.imageio import encode_jpeg

    v, stub = _stubbed_verifier(query_mode="center", center_size=640)
    data = encode_jpeg(np.zeros((1000, 2000, 3), dtype=np.uint8))

    v.read_query_text(data)

    assert stub.seen[0].shape == (425, 640, 3)  # 930x1400 -> downscale до максимума 640


def test_read_query_text_detector_mode_feeds_normalized_square_regardless_of_input_shape():
    """Режим "detector" (дефолт): `normalize_query()` ВСЕГДА отдаёт квадратный
    канонический канвас (448x448 по умолчанию), какой бы ни была форма входа —
    после даунскейла до `ocr_size` (320) движок видит 320x320, не форму входа."""
    from cv.imageio import encode_jpeg

    v, stub = _stubbed_verifier(query_mode="detector", ocr_size=320)
    data = encode_jpeg(np.zeros((1000, 2000, 3), dtype=np.uint8))

    v.read_query_text(data)

    assert stub.seen[0].shape == (320, 320, 3)


def test_read_query_text_center_on_corrupt_bytes_raises_value_error():
    v = LabelVerifier(query_mode="center")
    with pytest.raises(ValueError):
        v.read_query_text_center(b"not an image, just garbage bytes 0123456789")
    with pytest.raises(ValueError):
        v.read_query_text_center(b"")


def test_read_query_text_dispatches_to_center_when_mode_is_center(monkeypatch, synthetic_bottle_image):
    from cv.imageio import encode_jpeg

    calls: list[bytes] = []

    def _fake_center(self, image):
        calls.append(image)
        return "CENTER-RESULT"

    monkeypatch.setattr(LabelVerifier, "read_query_text_center", _fake_center)
    v = LabelVerifier(query_mode="center")
    data = encode_jpeg(synthetic_bottle_image)

    assert v.read_query_text(data) == "CENTER-RESULT"
    assert calls == [data]


def test_read_query_text_does_not_dispatch_to_center_when_mode_is_detector(monkeypatch, synthetic_bottle_image):
    """Регресс: дефолтный режим НЕ трогает `read_query_text_center()` вовсе —
    маршрутизация однонаправленная, не пробует оба пути."""
    from cv.imageio import encode_jpeg

    def _boom(self, image):
        raise AssertionError("read_query_text_center() не должен вызываться в режиме detector")

    monkeypatch.setattr(LabelVerifier, "read_query_text_center", _boom)
    v, _stub = _stubbed_verifier()  # дефолт "detector"
    data = encode_jpeg(synthetic_bottle_image)

    v.read_query_text(data)  # не должно поднять AssertionError выше


def test_verify_internal_fallback_ignores_ocr_query_mode(monkeypatch, synthetic_bottle_image):
    """agents/H1-cpu-path.md п.1: 'Верификатор near-dup (verify) получает тот же
    текст — как сейчас' — собственный fallback `verify()` (когда `ocr_text` не
    передан) по-прежнему читает ЧЕРЕЗ `normalize_query()` (детектор), НЕЗАВИСИМО
    от `CV_OCR_QUERY_MODE`. Режим влияет ТОЛЬКО на `read_query_text()`."""
    from cv.imageio import encode_jpeg

    monkeypatch.setenv("CV_OCR_QUERY_MODE", "center")
    calls: list[tuple[tuple[int, ...], int | None]] = []

    def _fake_read_text(self, image_arr, *, size=None):
        calls.append((image_arr.shape, size))
        return ""

    monkeypatch.setattr(LabelVerifier, "read_text", _fake_read_text)
    v = LabelVerifier()
    assert v.ocr_query_mode == "center"
    data = encode_jpeg(synthetic_bottle_image)

    v.verify(data, [{"slug": "x", "name": "x", "vintage": None}])

    assert len(calls) == 1
    shape, size = calls[0]
    assert shape == (448, 448, 3)  # normalize_query() дефолтный NORM_SIZE_DEFAULT — детектор, не центр-кроп
    assert size is None  # verify() не передаёт size= — read_text() сам использует self.ocr_size


# --- agents/H2-rapidocr-multiscale.md, задача 2: CV_OCR_ENGINE/CV_OCR_RAPID_SIZES ---
#
# Все тесты этого раздела — БЕЗ реального RapidOCR (брифа п.5: "юнит-тесты не должны
# требовать rapidocr/onnxruntime") — движок подменяется напрямую (`v._rapid = ...`)
# или методы диспетчеризации — monkeypatch, та же дисциплина, что H1-раздел выше
# применяет к PaddleOCR/`_StubOCR`.


def test_default_ocr_engine_is_paddle():
    """Дефолт брифа: 'paddle до приёмки' — молчаливой смены поведения быть не должно."""
    assert LabelVerifier().ocr_engine == "paddle"


def test_ocr_engine_env_var_overrides_constructor_default(monkeypatch):
    monkeypatch.setenv("CV_OCR_ENGINE", "rapid")
    assert LabelVerifier().ocr_engine == "rapid"


def test_ocr_engine_env_var_is_case_and_whitespace_normalized(monkeypatch):
    monkeypatch.setenv("CV_OCR_ENGINE", " RAPID \n")
    assert LabelVerifier().ocr_engine == "rapid"


def test_default_rapid_sizes_is_640_960():
    assert LabelVerifier().rapid_sizes == (640, 960)


def test_rapid_sizes_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("CV_OCR_RAPID_SIZES", "640,800,960")
    assert LabelVerifier().rapid_sizes == (640, 800, 960)


def test_rapid_sizes_constructor_param_overrides_default():
    assert LabelVerifier(rapid_sizes=(960,)).rapid_sizes == (960,)


def test_rapid_sizes_env_var_overrides_constructor_param(monkeypatch):
    monkeypatch.setenv("CV_OCR_RAPID_SIZES", "1280")
    assert LabelVerifier(rapid_sizes=(640,)).rapid_sizes == (1280,)


def test_read_query_text_dispatches_to_rapid_when_engine_is_rapid(monkeypatch, synthetic_bottle_image):
    from cv.imageio import encode_jpeg

    calls: list[bytes] = []

    def _fake_rapid(self, image):
        calls.append(image)
        return "RAPID-RESULT"

    monkeypatch.setattr(LabelVerifier, "read_query_text_rapid", _fake_rapid)
    v = LabelVerifier(engine="rapid")
    data = encode_jpeg(synthetic_bottle_image)

    assert v.read_query_text(data) == "RAPID-RESULT"
    assert calls == [data]


def test_read_query_text_does_not_dispatch_to_rapid_when_engine_is_paddle(monkeypatch, synthetic_bottle_image):
    """Регресс: дефолтный движок "paddle" НЕ трогает `read_query_text_rapid()` вовсе."""
    from cv.imageio import encode_jpeg

    def _boom(self, image):
        raise AssertionError("read_query_text_rapid() не должен вызываться в режиме paddle")

    monkeypatch.setattr(LabelVerifier, "read_query_text_rapid", _boom)
    v, _stub = _stubbed_verifier()  # дефолт "paddle"/"detector"
    data = encode_jpeg(synthetic_bottle_image)

    v.read_query_text(data)  # не должно поднять AssertionError выше


def test_read_query_text_rapid_mode_ignores_center_query_mode_setting(monkeypatch, synthetic_bottle_image):
    """agents/H2-rapidocr-multiscale.md п.2: `CV_OCR_ENGINE=rapid` не читает
    `CV_OCR_QUERY_MODE` вовсе — даже явный 'center' (или 'detector') не должен
    маршрутизировать в `read_query_text_center()` (тот — PaddleOCR)."""
    from cv.imageio import encode_jpeg

    def _boom(self, image):
        raise AssertionError("read_query_text_center() (PaddleOCR) не должен вызываться в режиме rapid")

    monkeypatch.setattr(LabelVerifier, "read_query_text_center", _boom)
    calls: list[bytes] = []
    monkeypatch.setattr(
        LabelVerifier, "read_query_text_rapid", lambda self, image: (calls.append(image), "ok")[1]
    )
    v = LabelVerifier(engine="rapid", query_mode="center")  # оба режима заданы явно
    data = encode_jpeg(synthetic_bottle_image)

    assert v.read_query_text(data) == "ok"
    assert calls == [data]


def test_read_query_text_rapid_decodes_and_delegates_to_reader_read_center(monkeypatch, synthetic_bottle_image):
    from cv.imageio import decode_image, encode_jpeg

    class _StubReader:
        def __init__(self):
            self.seen: list[np.ndarray] = []

        def read_center(self, arr):
            self.seen.append(arr)
            return "ok"

    v = LabelVerifier(engine="rapid")
    stub = _StubReader()
    v._rapid = stub  # пропускаем _rapid_reader()/реальный RapidOcrReader целиком
    data = encode_jpeg(synthetic_bottle_image)

    result = v.read_query_text_rapid(data)

    assert result == "ok"
    assert len(stub.seen) == 1
    assert np.array_equal(stub.seen[0], decode_image(data))


def test_read_query_text_rapid_on_corrupt_bytes_raises_value_error():
    v = LabelVerifier(engine="rapid")
    with pytest.raises(ValueError):
        v.read_query_text_rapid(b"not an image, just garbage bytes 0123456789")
    with pytest.raises(ValueError):
        v.read_query_text_rapid(b"")


def test_rapid_reader_created_lazily_once_and_cached(monkeypatch):
    """Брифа п.1: 'ленивая загрузка, один экземпляр движка на масштаб' — на уровне
    LabelVerifier это означает ОДИН `RapidOcrReader` на инстанс верификатора,
    переиспользуемый между вызовами, не пересоздаваемый каждый раз."""
    import cv.ocr_rapid as ocr_rapid_module

    construct_calls = {"n": 0}

    class _StubReader:
        def __init__(self, sizes):
            construct_calls["n"] += 1
            self.sizes = sizes

        def read_center(self, arr):
            return ""

    monkeypatch.setattr(ocr_rapid_module, "RapidOcrReader", _StubReader)
    v = LabelVerifier(engine="rapid", rapid_sizes=(640, 960))

    r1 = v._rapid_reader()
    r2 = v._rapid_reader()

    assert r1 is r2
    assert construct_calls["n"] == 1
    assert r1.sizes == (640, 960)


def test_read_query_text_rapid_never_touches_paddleocr_load(monkeypatch, synthetic_bottle_image):
    """agents/H2-rapidocr-multiscale.md: 'PaddleOCR при rapid НЕ грузится на
    прогреве' — на уровне `read_query_text()` это значит `_load()`/`self._ocr`
    (PaddleOCR) вообще не затрагиваются в режиме rapid."""
    from cv.imageio import encode_jpeg

    class _StubReader:
        def read_center(self, arr):
            return "стаб-текст"

    def _boom(self):
        raise AssertionError("_load() (PaddleOCR) не должен вызываться в режиме rapid")

    monkeypatch.setattr(LabelVerifier, "_load", _boom)
    v = LabelVerifier(engine="rapid")
    v._rapid = _StubReader()
    data = encode_jpeg(synthetic_bottle_image)

    text = v.read_query_text(data)

    assert text == "стаб-текст"
    assert v._ocr is None  # PaddleOCR так и не тронут


def test_verify_internal_fallback_ignores_ocr_engine(monkeypatch, synthetic_bottle_image):
    """agents/H2-rapidocr-multiscale.md: verify()'s собственный fallback (когда
    `ocr_text` не передан) ВСЕГДА PaddleOCR через `normalize_query()`, независимо
    от `CV_OCR_ENGINE=rapid` — та же дисциплина, что H1 уже установил для
    `CV_OCR_QUERY_MODE` (см. `test_verify_internal_fallback_ignores_ocr_query_mode`
    выше). Режим/движок влияют ТОЛЬКО на `read_query_text()`."""
    from cv.imageio import encode_jpeg

    monkeypatch.setenv("CV_OCR_ENGINE", "rapid")
    calls: list[tuple[tuple[int, ...], int | None]] = []

    def _fake_read_text(self, image_arr, *, size=None):
        calls.append((image_arr.shape, size))
        return ""

    monkeypatch.setattr(LabelVerifier, "read_text", _fake_read_text)
    v = LabelVerifier()
    assert v.ocr_engine == "rapid"
    data = encode_jpeg(synthetic_bottle_image)

    v.verify(data, [{"slug": "x", "name": "x", "vintage": None}])

    assert len(calls) == 1
    shape, size = calls[0]
    assert shape == (448, 448, 3)  # normalize_query() — fallback verify() никогда не берёт RapidOCR
    assert size is None
