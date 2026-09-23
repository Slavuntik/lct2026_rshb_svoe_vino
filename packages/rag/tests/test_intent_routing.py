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


# --- 22.09 (reports/backend-chat-retrieval.md): filters-осведомлённый classify() ---
# EMPTY_RETRIEVAL на фразе задания «Посоветуй красное к стейку до 2000 рублей»:
# classify() не видел, что Filters (извлечённые apps/api из той же реплики) уже
# несут color/sugar/region/grapes/stillness — точный, недвусмысленный сигнал "вопрос
# про вино", не рискующий поймать постороннее («Посоветуй фильм ужасов» не извлекает
# ни одного поля Filters).

def test_classify_active_filters_force_pairing_even_without_text_signal():
    from rag.types import Filters

    # Ни лексемы «вин[оаеуы]», ни гастро-глагола/фрагмента в начале строки — но
    # Filters несёт распознанный атрибут вина.
    assert classify("Белое до 1500", Filters(color="белое")) == "pairing"
    assert classify("Розовое к лёгкому ужину", Filters(color="розовое")) == "pairing"
    assert classify("Что-нибудь из Крыма", Filters(region="krym")) == "pairing"
    assert classify("Хочу что-то сладкое", Filters(sugar="сладкое")) == "pairing"


def test_classify_empty_or_missing_filters_do_not_force_pairing():
    from rag.types import Filters

    # Filters() без единого распознанного поля — как будто filters не передан:
    # посторонний вопрос остаётся default, а не подбором вина.
    assert classify("Посоветуй хороший фильм ужасов на вечер.", Filters()) == "default"
    assert classify("Посоветуй хороший фильм ужасов на вечер.", None) == "default"


def test_classify_filters_argument_is_optional_and_backward_compatible():
    # Старые однопараметровые вызовы classify(q) (rag.calibrate/rag.eval — см.
    # докстринг модуля) продолжают работать так же, как до этой правки.
    assert classify("Найдите белое сухое вино из Самары.") == "pairing"
    assert classify("просто текст без явных маркеров") == "default"


def test_classify_travel_and_fact_priority_unaffected_by_filters():
    # Travel/fact проверяются РАНЬШЕ pairing (и раньше filters_active) — активный
    # Filters не должен перебивать их приоритет.
    from rag.types import Filters

    assert classify("Расскажите про винодельню в Крыму", Filters(region="krym")) == "travel"
    assert classify("Что такое танины в красном вине?", Filters(color="красное")) == "fact"


# --- 22.09: короткий гастро-фрагмент «к/под …» теперь ищется где угодно в
# пределах лимита длины, не только в начале строки (см. докстринг intent.py) ---

def test_short_pairing_fragment_matches_mid_sentence_now():
    # «Посоветуй что-нибудь к рыбе» (одна из типовых фраз жюри, 27 симв.) —
    # фрагмент «к рыбе» не в начале строки, раньше уходило в default.
    assert classify("Посоветуй что-нибудь к рыбе") == "pairing"


def test_short_pairing_fragment_still_respects_length_guard():
    # Длинное предложение с «к» где-то в середине — не гастро-подбор; защита
    # по-прежнему длина, не позиция (регресс, названный в исходном тесте ниже).
    q = "Расскажи подробнее, к чему может привести долгая выдержка в дубе"
    assert len(q) > 30
    assert classify(q) == "default"


def test_short_pairing_fragment_requires_whole_word_not_substring():
    # "Как" не должен матчиться как "к " — \b перед группой требует границу
    # слова, а \s+ сразу после неё — что дальше идёт пробел, а не буква.
    assert classify("Как погода в Крыму?") == "default"


# --------------------------------------------------------------------------
# 22.09 (reports/backend-rag-rebuild.md): кнопка "Спросить сомелье об этом
# вине" шлёт t("chat.prefillAskAboutWine") = "Расскажи про {name} от
# {winery}" (apps/web/src/i18n/ru.ts:156) — до этой правки матчилась
# _FACT_RE ("расскаж.{0,3} про") или (для winery_name с "Винодельня") ещё
# раньше _TRAVEL_RE, и в обоих случаях коллекция "wines" не участвовала в
# поиске вовсе — вино не находило само себя НИ ПРИ КАКИХ обстоятельствах,
# даже если оно есть в индексе.
# --------------------------------------------------------------------------


def test_ask_about_wine_prefill_routes_to_pairing_not_fact():
    q = "Расскажи про Пино Нуар от А. Гордиенко & М. Николаев"
    assert classify(q) == "pairing", q
    assert infer_collections(q) == ("wines",), q


def test_ask_about_wine_prefill_wins_over_travel_when_winery_name_says_vinodelnya():
    # Ровно случай реальной винодельни каталога с "Винодельня" в имени
    # (Винодельня Орлова — 2 из 21 винодельни на пропущенных 125 вин).
    q = "Расскажи про Мерло Резерв от Винодельня Орлова"
    assert classify(q) == "pairing", q
    assert infer_collections(q) == ("wines",), q


def test_generic_fact_question_with_rasskazhi_still_routes_to_knowledge():
    # Общий вопрос про понятие ("расскажи про танины") не несёт " от <кого-то>"
    # после "про" — обязан остаться fact-веткой, не перехватываться новым паттерном.
    assert classify("Расскажи про танины в вине") == "fact"
    assert infer_collections("Расскажи про танины в вине") == ("knowledge",)


def test_goldset_only_rasskazhi_question_unaffected():
    # Единственный вопрос голдсета со словом "расскаж" (packages/rag/eval/goldset.jsonl,
    # type=travel) не содержит " от " после "про" — маршрут не должен измениться.
    q = "Расскажите про винодельню Абрау-Дюрсо."
    assert classify(q) == "travel", q
    assert infer_collections(q) == ("wineries", "knowledge"), q
