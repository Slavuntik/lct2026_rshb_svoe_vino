"""Лёгкая эвристика типа вопроса -> коллекции для дефолта search() (контракт
v0.3, ревью 02, п.3). НЕ полноценный NLU — тот остаётся ответственностью
вызывающего (см. v0.2.4: «извлечение Filters из реплики — ответственность
/chat»). Это только грубая маршрутизация по ключевым словам, чтобы дефолтный
search() без явных collections не мешал в одну кучу карточки вина и статьи
(см. отчёт A: «вытеснение карточек вина статьями» — hit@8 pick/pairing
разваливался именно от этого при дефолте contracts/rag-interface.md v0.2).

Приоритет проверок: travel -> fact -> pairing/pick -> дефолт контракта
(wines+knowledge), первое совпадение побеждает.

Pairing-подобные запросы (приёмка F, лог демо-сцены 2 — «Что взять к сырам?»
приносил 8 цитат ИЗ СТАТЕЙ вместо карточек вин) размечаются ОТДЕЛЬНО от
travel/fact: `classify()` возвращает `"pairing"`, а не готовый кортеж
коллекций — `Retriever.search()` реализует для него приоритет «wines
приоритетно, knowledge — добивкой» (см. `PAIRING_PRIMARY`/`PAIRING_FALLBACK`
и докстринг `Retriever.search`), а не жёсткое исключение knowledge: если
вин в фильтрованном пуле не хватило на top_k, остаток добирается статьями,
а не наоборот.
"""
from __future__ import annotations

import re

_TRAVEL_RE = re.compile(
    r"винодельн|вино.?тур|винный тур|экскурси|куда (съездить|поехать)|"
    r"путешеств|посетить|дегустационн.?(зал|комнат)",
    re.IGNORECASE,
)
_FACT_RE = re.compile(
    r"^что такое|^почему|^откуда|^как называ|^зачем|что означает|в чём разниц|"
    r"чем отлича|из чего (сделан|образ)|расскаж.{0,3} про|как понять",
    re.IGNORECASE,
)
_PAIRING_RE = re.compile(
    r"вин[оаеуы]\b|"  # «вино/вина/вине/вину/вины» — сама лексема, не «винодельня» (та ловится travel-веткой раньше)
    r"что (подать|взять|выбрать|заказать)\b",  # гастропара без слова «вино»: «что взять к сырам»
    re.IGNORECASE,
)
# ПРИМЕЧАНИЕ: голые глаголы-рекомендации («посоветуй», «подбери», «найди» без
# упоминания вина/еды) сюда НЕ входят — были в первой версии правки и ловили
# «Посоветуй фильм ужасов» в pairing, из-за чего эта конкретная garbage-фраза
# обходила refusal (см. Retriever._search_pairing) и hit@8 голд-сета просел
# 0.8267->0.8133. Проверено: все 30 pick/pairing вопросов голд-сета и так
# содержат лексему «вин[оаеуы]» или «что подать/взять/выбрать/заказать» — эти
# глаголы не добавляли покрытия, только ложные срабатывания на не-винных фразах.
# Терпимый гастро-фрагмент без глагола — реплика-продолжение в чате
# («к устрицам», «под стейк»): предлог в начале короткой фразы. Порог длины
# отсекает длинные предложения, где «к»/«под» — обычный предлог не по теме
# (иначе поймали бы полкаталога фактов не туда).
_SHORT_PAIRING_FRAGMENT_RE = re.compile(r"^\s*(к|под)\s+\S", re.IGNORECASE)
_SHORT_FRAGMENT_MAX_LEN = 30

DEFAULT_COLLECTIONS: tuple[str, ...] = ("wines", "knowledge")
PAIRING_PRIMARY: tuple[str, ...] = ("wines",)
PAIRING_FALLBACK: tuple[str, ...] = ("wines", "knowledge")


def _is_pairing_like(q: str) -> bool:
    if _PAIRING_RE.search(q):
        return True
    return len(q) <= _SHORT_FRAGMENT_MAX_LEN and bool(_SHORT_PAIRING_FRAGMENT_RE.match(q))


def classify(query: str) -> str:
    """travel | fact | pairing | default — первое совпадение по приоритету."""
    q = (query or "").strip()
    if not q:
        return "default"
    if _TRAVEL_RE.search(q):
        return "travel"
    if _FACT_RE.search(q):
        return "fact"
    if _is_pairing_like(q):
        return "pairing"
    return "default"


def infer_collections(query: str) -> tuple[str, ...]:
    """Готовый кортеж collections для однократного вызова search() — используется
    везде, КРОМЕ pairing (там нужен приоритет с добивкой, а не один кортеж,
    см. Retriever.search и docstring модуля)."""
    kind = classify(query)
    if kind == "travel":
        return ("wineries", "knowledge")
    if kind == "fact":
        return ("knowledge",)
    if kind == "pairing":
        return PAIRING_PRIMARY
    return DEFAULT_COLLECTIONS
