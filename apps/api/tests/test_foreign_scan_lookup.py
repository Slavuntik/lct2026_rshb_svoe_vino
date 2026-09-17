"""Юнит-тесты словарного лукапа app/foreign_scan_lookup.py — на настоящих
pipeline/ref/{grape_synonyms,reference_styles}.yaml (read-only, agents/
B7-foreign-analogs.md), без похода через HTTP/ретривер. Точный сценарий
"risling/riesling" из брифа случайно решается уже ТОЧНЫМ совпадением по
слагу сорта ("risling" — сам слаг рислинга в справочнике) — тест на
"Rieslin" (нет такого слага) отдельно нагружает именно опечаточный
(edit distance <= 1) путь, не эту случайность.
"""
from __future__ import annotations

from pathlib import Path

from app.foreign_scan_lookup import ForeignRefLookup

REF_DIR = Path(__file__).resolve().parents[3] / "pipeline" / "ref"


def _lookup() -> ForeignRefLookup:
    return ForeignRefLookup(REF_DIR)


def test_grape_synonym_exact_match_urban_risling():
    match = _lookup().find("Urban Risling")
    assert match is not None
    assert match.label == "Рислинг"
    assert match.resolve_query == "Рислинг"


def test_grape_typo_tolerance_without_lucky_slug_coincidence():
    """"Rieslin" (пропущена конечная "g") не совпадает ТОЧНО ни со слагом
    ("risling"), ни с синонимом ("Riesling") — только edit distance 1
    находит сорт. Доказывает, что опечаточная устойчивость — не побочный
    эффект того, что слаг сорта сам выглядит как опечатка."""
    match = _lookup().find("Rieslin")
    assert match is not None
    assert match.label == "Рислинг"


def test_style_slug_match_chianti_derives_grape():
    """"Chianti" — не сорт (в grape_synonyms.yaml его нет), а аппелласьон
    (reference_styles.yaml: chianti-classico, grapes: [Санджовезе])."""
    match = _lookup().find("Chianti")
    assert match is not None
    assert match.label == "Санджовезе"
    assert match.resolve_query == "Кьянти Классико"


def test_no_match_on_gibberish():
    assert _lookup().find("абракадабра") is None


def test_no_false_positive_on_generic_english_wine_words():
    """Регресс на находку в этой же волне: однословные ключи стилевого
    индекса берутся ТОЛЬКО из slug (латиница), не из name (кириллическая
    проза) — иначе общеупотребимое "вино" (из "Вино Нобиле ди
    Монтепульчано") ловило бы произвольный русский текст про вино, а
    "wine"/"port"/"left"/"light" из слагов — произвольный английский."""
    for text in (
        "zzz qqq garbage не вино вообще 12345",  # tests/test_scan.py: сценарий низкой уверенности
        "мне понравилось красное сухое вино вчера на дегустации",
        "I like this wine", "nice light wine", "left bank please",
    ):
        assert _lookup().find(text) is None, text


def test_no_match_on_empty_text():
    assert _lookup().find("") is None
