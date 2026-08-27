"""Лёгкая эвристика типа вопроса -> коллекции, встроенная в search() как
дефолт при непереданном collections (контракт v0.3, ревью 02, п.3). Явно
переданный collections эвристику отключает — воля вызывающего побеждает.
"""
from __future__ import annotations

from rag.intent import infer_collections


def test_travel_queries_route_to_wineries_and_knowledge():
    for q in [
        "Расскажите про винодельню Абрау-Дюрсо.",
        "Какие винодельни можно посетить в Крыму?",
        "Куда поехать за вином, какой винный тур выбрать?",
    ]:
        assert infer_collections(q) == ("wineries", "knowledge"), q


def test_fact_queries_route_to_knowledge_only():
    for q in [
        "Что такое танины в вине?",
        "Почему вино может быть мутным?",
        "В чём разница между ароматикой и букетом?",
        "Зачем виноделы используют бетонные ёмкости?",
    ]:
        assert infer_collections(q) == ("knowledge",), q


def test_pick_and_pairing_queries_route_to_wines_only():
    for q in [
        "Посоветуйте сухое красное вино из Кубани.",
        "Что подать к устрицам?",
        "Какое вино взять к мясу?",
        "Найдите белое сухое вино из Самары.",
    ]:
        assert infer_collections(q) == ("wines",), q


def test_ambiguous_query_falls_back_to_contract_default():
    assert infer_collections("просто текст без явных маркеров") == ("wines", "knowledge")
    assert infer_collections("") == ("wines", "knowledge")
    assert infer_collections(None) == ("wines", "knowledge")


def test_travel_checked_before_pick_for_wino_substring_in_winodelnya():
    # "винодельню" содержит подстроку "вино" — travel-ветка должна выигрывать
    # у pick-ветки за счёт порядка проверок, иначе "вино" перетянет на wines-only.
    assert infer_collections("Расскажите про винодельню") == ("wineries", "knowledge")


def test_search_default_uses_heuristic_when_collections_not_given(tiny_index):
    # Фактическая проверка: белое вино к рыбе, вопрос сформулирован как pick ->
    # эвристика должна ограничить поиск коллекцией wines (не мешать со статьями).
    results = tiny_index.search("Найдите белое сухое вино из Крыма")
    assert results
    for c in results:
        assert c.kind == "wine"


def test_search_explicit_collections_overrides_heuristic(tiny_index):
    # Тот же явно "pick"-вопрос, но с явным collections=("knowledge",) —
    # вызывающий сильнее эвристики.
    results = tiny_index.search("Найдите белое сухое вино из Крыма", collections=("knowledge",))
    for c in results:
        assert c.kind == "chunk"
