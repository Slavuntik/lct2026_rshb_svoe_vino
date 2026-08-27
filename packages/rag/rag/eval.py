"""Eval-раннер по голд-сету: hit@k, MRR, замер латентности search().

Диспетчеризация по типу вопроса (contracts/rag-interface.md):
  pick / pairing -> Retriever.search(q, collections=("wines",), top_k=k)
  fact           -> Retriever.search(q, collections=("knowledge",), top_k=k)
  travel         -> Retriever.search(q, collections=("wineries","knowledge"), top_k=k)
  analog         -> resolve_style(q) -> analog_for_style(slug, top_k=k)
    (если стиль не распознан — считается промахом; так честно проверяется
    вся цепочка «реплика -> стиль -> аналог», а не только retrieval).

Почему не дефолтный collections=("wines","knowledge") контракта для всех
типов: замер на реальном каталоге показал явное «вытеснение» карточек вина
длинными статьями на ту же тему («вино из Сибирьковый» -> просветительская
статья «Сибирьковый: что за сорт» обгоняет саму карточку вина и по
dense, и по BM25, и после реранка — статья длиннее и лексически «на тему»
сильнее, чем терпимая карточка «Название · регион · сорт · вкус»). Роутинг
по типу вопроса — это ровно то, для чего в контракте есть параметр
`collections` со свободным дефолтом: реальный продукт тоже сначала
определяет намерение (рекомендация/факт/поездка), а потом решает, где
искать. Без роутинга hit@8 разваливается на pick/pairing/travel не из-за
качества ranking, а из-за конкуренции коллекций — см. отчёт.
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
    expect = set(expect_ids)
    got = set(retrieved_ids)
    if expect_any:
        return 1 if expect & got else 0
    return 1 if expect.issubset(got) else 0


def _reciprocal_rank(expect_ids: list[str], retrieved_ids: list[str]) -> float:
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


def run_question(retriever: Retriever, q: dict, top_k: int) -> list[str]:
    qtype = q.get("type")
    text = q["q"]
    if qtype == "analog":
        style = retriever.resolve_style(text)
        if style is None:
            return []
        cands = retriever.analog_for_style(style["slug"], top_k=top_k)
    else:
        collections = _TYPE_COLLECTIONS.get(qtype, ("wines", "knowledge"))
        cands = retriever.search(text, collections=collections, top_k=top_k)
    return [c.id for c in cands]


def evaluate(retriever: Retriever, goldset: list[dict], *, top_k: int = 8) -> dict:
    per_type: dict[str, dict[str, list]] = {}
    hits: list[int] = []
    rr: list[float] = []
    details = []

    for q in goldset:
        retrieved = run_question(retriever, q, top_k)
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
