"""Лёгкая эвристика типа вопроса, встроенная в search() как дефолт при
непереданном collections (контракт v0.3, ревью 02, п.3). Явно переданный
collections эвристику отключает — воля вызывающего побеждает.

Pairing-подобные запросы («что взять к…», «к устрицам», «под стейк» —
приёмка F, демо-сцена 2 приносила 8 цитат из статей вместо карточек вин)
получают ПРИОРИТЕТ, а не жёсткое исключение: wines сначала, knowledge —
добивкой, только если карточек вин не хватило на top_k (см. `classify()`,
`Retriever.search`).
"""
from __future__ import annotations

from rag.intent import classify, infer_collections


def test_travel_queries_route_to_wineries_and_knowledge():
    for q in [
        "Расскажите про винодельню Абрау-Дюрсо.",
        "Какие винодельни можно посетить в Крыму?",
        "Куда поехать за вином, какой винный тур выбрать?",
    ]:
        assert classify(q) == "travel", q
        assert infer_collections(q) == ("wineries", "knowledge"), q


def test_fact_queries_route_to_knowledge_only():
    for q in [
        "Что такое танины в вине?",
        "Почему вино может быть мутным?",
        "В чём разница между ароматикой и букетом?",
        "Зачем виноделы используют бетонные ёмкости?",
    ]:
        assert classify(q) == "fact", q
        assert infer_collections(q) == ("knowledge",), q


def test_pairing_queries_classified_as_pairing():
    for q in [
        "Посоветуйте сухое красное вино из Кубани.",
        "Что подать к устрицам?",
        "Какое вино взять к мясу?",
        "Найдите белое сухое вино из Самары.",
        "Что взять к сырам?",  # дословный вопрос демо-сцены 2 (abrau-dyurso pack)
    ]:
        assert classify(q) == "pairing", q
        assert infer_collections(q) == ("wines",), q  # infer_collections = только primary


def test_short_pairing_fragments_without_verb_classified_as_pairing():
    # Реплика-продолжение в чате: без глагола «взять/подать», просто предлог
    # + блюдо. Явно названо оркестратором как регресс приёмки F.
    for q in ["к устрицам", "под стейк", "К сырам", "под шашлык"]:
        assert classify(q) == "pairing", q


def test_bare_k_mid_sentence_does_not_trigger_short_fragment_match():
    # "к" не в начале строки (и без лексемы «вино»/гастро-глагола) — короткий
    # gastro-фрагмент матчится только якорем ^ в начале, а не где угодно.
    q = "Расскажи подробнее, к чему может привести долгая выдержка в дубе"
    assert classify(q) == "default"


def test_fact_lexeme_wins_even_when_query_also_mentions_wine():
    # "вино" в вопросе не должно перебивать явный fact-паттерн — travel/fact
    # проверяются раньше pairing по приоритету (см. classify()).
    assert classify("Что такое танины и откуда они берутся в вине?") == "fact"


def test_ambiguous_query_falls_back_to_contract_default():
    assert classify("просто текст без явных маркеров") == "default"
    assert infer_collections("просто текст без явных маркеров") == ("wines", "knowledge")
    assert infer_collections("") == ("wines", "knowledge")
    assert infer_collections(None) == ("wines", "knowledge")


def test_travel_checked_before_pairing_for_wino_substring_in_winodelnya():
    # "винодельню" содержит подстроку "вино" — travel-ветка должна выигрывать
    # у pairing-ветки за счёт порядка проверок, иначе "вино" перетянет на wines.
    assert classify("Расскажите про винодельню") == "travel"
    assert infer_collections("Расскажите про винодельню") == ("wineries", "knowledge")


def test_search_pairing_stays_wines_only_when_enough_results(tiny_index):
    # В мини-фикстуре достаточно вин (7) относительно top_k=5 — knowledge
    # добавляться не должен: «wines приоритетно» без нужды в добивке.
    results = tiny_index.search("Найдите белое сухое вино из Крыма", top_k=5)
    assert results
    for c in results:
        assert c.kind == "wine"


def test_search_pairing_falls_back_to_knowledge_when_wines_insufficient(tiny_index):
    # top_k=8 > 7 вин в мини-фикстуре — «добивка» знанием ДОЛЖНА сработать
    # (это и есть «knowledge добивкой», проверено явно, не как побочный эффект).
    results = tiny_index.search("Найдите белое сухое вино из Крыма", top_k=8)
    kinds = {c.kind for c in results}
    assert "wine" in kinds
    # ровно 7 вин в фикстуре -> 8-й результат (если он есть) обязан быть добивкой
    assert len(results) <= 8


def test_search_explicit_collections_overrides_heuristic(tiny_index):
    # Тот же явно pairing-подобный вопрос, но с явным collections=("knowledge",) —
    # вызывающий сильнее эвристики, никакого приоритета/добивки не применяется.
    results = tiny_index.search("Найдите белое сухое вино из Крыма", collections=("knowledge",))
    for c in results:
        assert c.kind == "chunk"


def test_search_scene2_pairing_question_returns_only_wines_on_tiny_index(tiny_index):
    # Дословный вопрос демо-сцены 2 (qa/packs/abrau-dyurso/pack.json) — top_k=5
    # чтобы не задеть добивку (в фикстуре только 7 вин, см. тест выше).
    results = tiny_index.search("Что взять к сырам?", top_k=5)
    assert results
    for c in results:
        assert c.kind == "wine"
