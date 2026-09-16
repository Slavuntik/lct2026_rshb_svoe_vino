"""Тесты LabelVerifier — contracts/image-scan.md v0.4.4, ревью 04 блокер 1.

Разделены на:
- юниты на `match_candidates()` (чистая функция, без OCR-движка — быстрые, много кейсов);
- юниты на регэкспы извлечения токенов;
- интеграционные тесты на реальном `LabelVerifier` (настоящий PaddleOCR) — DoD брифа:
  near-dup пара aligote-2024/2025 различается по году на читаемом синтетическом
  ракурсе, воздержание на нечитаемом, битые байты -> ValueError, бюджет p95.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from cv.verify import (
    LabelVerifier,
    VerifyCandidate,
    _extract_categories,
    _extract_volumes,
    _extract_years,
    match_candidates,
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


def test_verify_p95_latency_budget(label_verifier):
    """Контракт: бюджет verify() <= 700 мс p95. Замер на читаемом ракурсе (худший
    случай по объёму работы OCR — есть что распознавать; на нечитаемом/пустом OCR
    обычно быстрее, т.к. меньше текстовых боксов проходит порог уверенности)."""
    from cv.augment import render_synthetic_views
    from cv.imageio import encode_jpeg
    from cv.verify import benchmark

    ref = _make_bottle_label(2024)
    views = render_synthetic_views(ref, n=_READABLE_VIEW_INDEX + 1, seed=_READABLE_VIEW_SEED)
    data = encode_jpeg(views[_READABLE_VIEW_INDEX])

    report = benchmark(label_verifier, [data], _ALIGOTE_CANDIDATES, n=8)
    assert report["p95_ms"] <= 700, f"verify() p95={report['p95_ms']}ms превышает бюджет 700мс: {report}"
