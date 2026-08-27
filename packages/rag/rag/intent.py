"""Лёгкая эвристика типа вопроса -> коллекции для дефолта search() (контракт
v0.3, ревью 02, п.3). НЕ полноценный NLU — тот остаётся ответственностью
вызывающего (см. v0.2.4: «извлечение Filters из реплики — ответственность
/chat»). Это только грубая маршрутизация по ключевым словам, чтобы дефолтный
search() без явных collections не мешал в одну кучу карточки вина и статьи
(см. отчёт A: «вытеснение карточек вина статьями» — hit@8 pick/pairing
разваливался именно от этого при дефолте contracts/rag-interface.md v0.2).

Приоритет проверок: travel -> fact -> pick/pairing -> дефолт контракта
(wines+knowledge), первое совпадение побеждает.
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
_PICK_RE = re.compile(
    r"вин[оаеуы]\b|"  # «вино/вина/вине/вину/вины» — сама лексема, не «винодельня» (та ловится travel-веткой раньше)
    r"что (подать|взять|выбрать|заказать)\b|"  # гастропара без слова «вино»
    r"посовету|подбер|подскаж|порекоменд|найд[иу]|выбер",
    re.IGNORECASE,
)

DEFAULT_COLLECTIONS: tuple[str, ...] = ("wines", "knowledge")


def infer_collections(query: str) -> tuple[str, ...]:
    q = (query or "").strip()
    if not q:
        return DEFAULT_COLLECTIONS
    if _TRAVEL_RE.search(q):
        return ("wineries", "knowledge")
    if _FACT_RE.search(q):
        return ("knowledge",)
    if _PICK_RE.search(q):
        return ("wines",)
    return DEFAULT_COLLECTIONS
