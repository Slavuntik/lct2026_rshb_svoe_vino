"""resolve_style (v0.2, добавлено по блокеру 3 ревью 01): «люблю Просекко» ->
slug эталонного стиля. Fuzzy по name/slug из настоящего ref/reference_styles.yaml
(read-only справочник vines) — резолвер стилей не зависит от ingest, но здесь
проверяется через настоящий контрактный метод Retriever.resolve_style на
фикстуре (StyleMatcher внутри неё всегда грузит реальный reference_styles.yaml,
мини-каталог вин на это не влияет).
"""
from __future__ import annotations


def test_resolve_exact_name(tiny_index):
    result = tiny_index.resolve_style("люблю Просекко")
    assert result == {"slug": "prosecco", "name": "Просекко", "country": "Италия"}


def test_resolve_typo(tiny_index):
    result = tiny_index.resolve_style("хочу шабьли")  # опечатка в "Шабли"
    assert result is not None
    assert result["slug"] == "chablis"


def test_resolve_paraphrase(tiny_index):
    result = tiny_index.resolve_style("нравится шампанское брют")
    assert result is not None
    assert "champagne" in result["slug"]


def test_resolve_garbage_returns_none(tiny_index):
    for garbage in ["погода в москве завтра", "картридж для принтера", "asdkjhaskjdh", ""]:
        assert tiny_index.resolve_style(garbage) is None, f"мусор должен давать None: {garbage!r}"
