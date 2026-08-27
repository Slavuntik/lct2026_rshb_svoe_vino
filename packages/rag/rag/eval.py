"""Eval-раннер по голд-сету: hit@k, MRR, замер латентности search().

Два режима роутинга коллекций (`routing=`), контракт v0.3 п.3:
  "oracle"    — коллекции берутся из ИЗВЕСТНОГО типа вопроса голд-сета
                (_TYPE_COLLECTIONS) — потолок качества retrieval-ядра,
                недостижимый в проде (прод не знает разметку голд-сета).
  "heuristic" — Retriever.search(q, top_k=k) БЕЗ явных collections: решает
                эвристика rag/intent.py внутри search() по умолчанию — ровно
                то, что вызовет прод (/chat), не зная типа вопроса заранее.
`analog` в обоих режимах не идёт через search(): resolve_style(q) ->
analog_for_style(slug, top_k=k) — если стиль не распознан, считается
промахом (честно проверяется вся цепочка «реплика -> стиль -> аналог»).

type="refusal" (посторонние вопросы, контракт v0.3 п.2): expect_ids=[],
успех = ПУСТАЯ выдача (см. _hit) — search() должен отказаться отвечать
на «как починить карбюратор», а не подсунуть LLM левый контекст для цитаты.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from rag.base import Retriever


def load_goldset(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _hit(expect_ids: list[str], expect_any: bool, retrieved_ids: list[str]) -> int:
    if not expect_ids:
        # type="refusal": ничего не ожидается -> успех = ПУСТАЯ выдача.
        # (иначе expect & got был бы пуст ВСЕГДА, и такой вопрос был бы
        # промахом по построению — что противоположно намерению теста).
        return 1 if not retrieved_ids else 0
    expect = set(expect_ids)
    got = set(retrieved_ids)
    if expect_any:
        return 1 if expect & got else 0
    return 1 if expect.issubset(got) else 0


def _reciprocal_rank(expect_ids: list[str], retrieved_ids: list[str]) -> float:
    if not expect_ids:
        return 1.0 if not retrieved_ids else 0.0
    expect = set(expect_ids)
    for i, rid in enumerate(retrieved_ids, start=1):
        if rid in expect:
            return 1.0 / i
    return 0.0


_TYPE_COLLECTIONS = {
    "pick": ("wines",),
    "pairing": ("wines",),
    "fact": ("knowledge",),
    "travel": ("wineries", "knowledge"),
}


def run_question(retriever: Retriever, q: dict, top_k: int, *, routing: str = "oracle") -> list[str]:
    qtype = q.get("type")
    text = q["q"]
    if qtype == "analog":
        style = retriever.resolve_style(text)
        if style is None:
            return []
        cands = retriever.analog_for_style(style["slug"], top_k=top_k)
        return [c.id for c in cands]

    if routing == "oracle":
        collections = _TYPE_COLLECTIONS.get(qtype, ("wines", "knowledge"))
        cands = retriever.search(text, collections=collections, top_k=top_k)
    elif routing == "heuristic":
        # Как реально позовёт прод: без явных collections — решает
        # rag.intent.infer_collections() внутри Retriever.search() (дефолт
        # контракта v0.3 п.3), не зная типа вопроса из разметки голд-сета.
        cands = retriever.search(text, top_k=top_k)
    else:
        raise ValueError(f"routing должен быть 'oracle' или 'heuristic', получено {routing!r}")
    return [c.id for c in cands]


def evaluate(retriever: Retriever, goldset: list[dict], *, top_k: int = 8, routing: str = "oracle") -> dict:
    per_type: dict[str, dict[str, list]] = {}
    hits: list[int] = []
    rr: list[float] = []
    details = []

    for q in goldset:
        retrieved = run_question(retriever, q, top_k, routing=routing)
        h = _hit(q["expect_ids"], q.get("expect_any", True), retrieved)
        r = _reciprocal_rank(q["expect_ids"], retrieved)
        hits.append(h)
        rr.append(r)

        t = q.get("type", "unknown")
        bucket = per_type.setdefault(t, {"hits": [], "rr": []})
        bucket["hits"].append(h)
        bucket["rr"].append(r)

        details.append(
            {
                "q": q["q"],
                "type": t,
                "hit": h,
                "rr": round(r, 4),
                "expect_ids": q["expect_ids"],
                "retrieved": retrieved,
            }
        )

    def _agg(hs, rs):
        n = len(hs) or 1
        return {"n": len(hs), f"hit_at_{top_k}": round(sum(hs) / n, 4), "mrr": round(sum(rs) / n, 4)}

    report = {
        "n": len(goldset),
        "top_k": top_k,
        "routing": routing,
        **_agg(hits, rr),
        "by_type": {t: _agg(v["hits"], v["rr"]) for t, v in sorted(per_type.items())},
        "details": details,
    }
    return report


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(len(sorted_values) * p + 0.5)) - 1)
    idx = max(0, idx)
    return sorted_values[idx]


def benchmark_latency(
    retriever: Retriever, queries: list[str], *, n: int = 100, use_reranker: bool = True, top_k: int = 8
) -> dict:
    if not queries:
        return {"n": 0, "use_reranker": use_reranker, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    timings = []
    for i in range(n):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        retriever.hybrid.search(q, top_k=top_k, use_reranker=use_reranker)
        timings.append((time.perf_counter() - t0) * 1000.0)
    timings.sort()
    return {
        "n": n,
        "use_reranker": use_reranker,
        "p50_ms": round(_percentile(timings, 0.50), 2),
        "p95_ms": round(_percentile(timings, 0.95), 2),
        "max_ms": round(timings[-1], 2),
    }
