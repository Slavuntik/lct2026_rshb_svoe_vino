"""Контрактная сверка MockRetriever с contracts/rag-interface.md v0.3:
форма Candidate.meta по kind, и нормативная сигнатура candidates_for_taste
(позиционный exclude_ids: list[str], limit: int = 20 — без keyword-only).
"""
from __future__ import annotations

import inspect

from app.rag.mock import MockRetriever


def test_wine_candidate_meta_has_source_and_derived_blocks():
    retriever = MockRetriever()
    candidate = retriever.get_by_id("shato-vymysel-cabernet")
    assert candidate is not None
    assert candidate.kind == "wine"
    assert set(candidate.meta.keys()) == {"source", "derived"}
    assert candidate.meta["source"]["name"] == "Шато Вымысел Каберне Совиньон"
    assert "sensory" in candidate.meta["derived"]
    # slug/derived/source_url не дублируются внутри source
    assert "derived" not in candidate.meta["source"]
    assert "source_url" not in candidate.meta["source"]
    assert "slug" not in candidate.meta["source"]


def test_chunk_candidate_meta_has_article_fields_not_wine_fields():
    retriever = MockRetriever()
    candidate = retriever.get_by_id("article:steak-pairing#1")
    assert candidate is not None
    assert candidate.kind == "chunk"
    assert set(candidate.meta.keys()) == {"article_id", "title", "heading", "rubric"}
    assert candidate.meta["article_id"] == "steak-pairing"
    assert candidate.meta["title"]


def test_search_wine_results_have_canonical_meta_shape():
    retriever = MockRetriever()
    results = retriever.search("Что подать к стейку?", collections=("wines",))
    assert results
    for c in results:
        assert set(c.meta.keys()) == {"source", "derived"}


def test_search_knowledge_results_have_canonical_meta_shape():
    retriever = MockRetriever()
    results = retriever.search("Что подать к стейку?", collections=("knowledge",))
    assert results
    for c in results:
        assert set(c.meta.keys()) == {"article_id", "title", "heading", "rubric"}


def test_resolve_label_similar_and_analog_all_use_same_wine_meta_shape():
    retriever = MockRetriever()
    for candidates in (
        retriever.resolve_label("Шато Вымысел"),
        retriever.similar("shato-vymysel-cabernet"),
        retriever.analog_for_style("valpolicella"),
        retriever.candidates_for_taste([], limit=20),
    ):
        assert candidates, "ожидались непустые результаты для проверки формы meta"
        for c in candidates:
            assert set(c.meta.keys()) == {"source", "derived"}


def test_search_default_collections_is_none_and_behaves_like_searching_everything():
    """contracts/rag-interface.md v0.3.3 (ревью 03, блокер заморозки):
    collections=None — дефолт и норма (intent-роутинг у настоящего
    ретривера агента A; мок просто ищет везде — см. app/rag/mock.py). Явный
    tuple("wines","knowledge") форсирует то же самое, но обходя роутинг —
    сигнатура обязана принимать None по умолчанию, не жёсткий tuple."""
    sig = inspect.signature(MockRetriever.search)
    assert sig.parameters["collections"].default is None

    retriever = MockRetriever()
    default_call = retriever.search("Что подать к стейку?")
    explicit_none = retriever.search("Что подать к стейку?", collections=None)
    forced_both = retriever.search("Что подать к стейку?", collections=("wines", "knowledge"))
    assert [c.id for c in default_call] == [c.id for c in explicit_none] == [c.id for c in forced_both]


def test_candidates_for_taste_signature_is_positional_not_keyword_only():
    """contracts/rag-interface.md v0.3: exclude_ids позиционный, не keyword-only —
    вызов позиционными аргументами обязан работать (это то, что делает
    routers/taste.py и настоящий packages/rag)."""
    sig = inspect.signature(MockRetriever.candidates_for_taste)
    params = list(sig.parameters.values())[1:]  # без self
    assert params[0].name == "exclude_ids"
    assert params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    assert params[1].name == "limit"
    assert params[1].default == 20

    # Позиционный вызов (как делает Retriever.candidates_for_taste в контракте):
    retriever = MockRetriever()
    result = retriever.candidates_for_taste(["shato-vymysel-cabernet"], 3)
    assert len(result) <= 3
    assert all(c.id != "shato-vymysel-cabernet" for c in result)
