"""Python-версия жёсткого фильтра — применяется к BM25-кандидатам и к
in-memory payload cache (resolve_style/analog_for_style), т.к. sparse-индекс
живёт отдельно от Qdrant и не умеет фильтровать по payload сам.

Семантика намеренно зеркалит rag.store.build_filter (тот же набор полей),
чтобы dense- и sparse-ветки пайплайна резали одинаковый набор кандидатов —
это то самое требование брифа «фильтры режут до векторов».
"""
from __future__ import annotations

from rag.types import Filters
from rag import refdata


def passes_filters(payload: dict, filters: Filters | None, *, include_wine_fields: bool = True) -> bool:
    if filters is None:
        return True
    f = payload.get("filters", {}) or {}

    if filters.region:
        wanted = refdata.normalize_region(filters.region)
        got = refdata.normalize_region(f.get("region"))
        if got != wanted:
            return False

    if include_wine_fields:
        if filters.color and f.get("color") != filters.color:
            return False
        if filters.sugar and f.get("sugar") != filters.sugar:
            return False
        if filters.stillness and f.get("stillness") != filters.stillness:
            return False
        if filters.grapes:
            have = set(f.get("grapes") or [])
            if not have.intersection(filters.grapes):
                return False

    return True
