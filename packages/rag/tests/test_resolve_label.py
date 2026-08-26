"""resolve_label: точное имя / опечатка / часть текста этикетки / мусор -> пусто.

Контракт: "fuzzy по name+winery_name (rapidfuzz), НЕ векторный поиск" —
используем настоящий Retriever.resolve_label поверх мини-фикстуры (7 вин),
чтобы проверить весь путь: ingest строит labels.jsonl -> rapidfuzz матчит.
"""
from __future__ import annotations


def test_exact_name_and_winery_matches_top(tiny_index):
    results = tiny_index.resolve_label("Тестовый Совиньон Блан Тестовая Винодельня Б")
    assert results, "точное совпадение должно найтись"
    assert results[0].id == "white-dry-fish-1"
    assert results[0].kind == "wine"
    assert results[0].meta["match_score"] >= 95
    assert results[0].meta["low_confidence"] is False


def test_typo_still_resolves(tiny_index):
    # "Совиньан Блан" (опечатка) + слегка урезанное название винодельни
    results = tiny_index.resolve_label("Тестовый Совиньан Блан Тестовая Виноделня")
    assert results, "опечатка не должна превращать запрос в пустой результат"
    assert results[0].id == "white-dry-fish-1"


def test_partial_label_text_with_noise_resolves(tiny_index):
    # Имитация OCR: имя+винодельня внутри мусора об урожае/объёме/крепости
    noisy = "ООО ЗАВОД Тестовая Винодельня Б Тестовое Шардоне Крю урожай 2023 0.75L 12.5% VOL"
    results = tiny_index.resolve_label(noisy)
    assert results, "часть текста этикетки должна находить вино"
    ids = [c.id for c in results]
    assert "white-dry-fish-2" in ids


def test_garbage_returns_empty(tiny_index):
    for garbage in [
        "погода в москве завтра",
        "картридж для принтера HP LaserJet",
        "ыфвафвафыв",
        "",
        "   ",
    ]:
        assert tiny_index.resolve_label(garbage) == [], f"мусор должен давать пусто: {garbage!r}"


def test_low_confidence_flag_present_in_meta(tiny_index):
    # Явный мусор с общими словами про вино — не должен пройти вообще (см. тест выше);
    # опечатка средней тяжести должна пройти, но может быть помечена low_confidence.
    results = tiny_index.resolve_label("Мерло Тестовая А")
    assert results
    assert "low_confidence" in results[0].meta
    assert isinstance(results[0].meta["low_confidence"], bool)
