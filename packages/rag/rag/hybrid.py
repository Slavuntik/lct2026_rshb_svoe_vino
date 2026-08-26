"""Гибридный retrieval: фильтры (жёсткие, до векторов) -> dense + sparse BM25
-> RRF-слияние -> опциональный реранкер top-N -> top-k. Плюс `similar()`,
которому нужен только dense-store (kNN по уже посчитанному вектору вина).

Канон пайплайна — contracts/rag-interface.md. Модели и обоснование выбора —
rag/config.py и отчёт.
"""
from __future__ import annotations

from rag import config
from rag.embeddings import BM25Index, DenseEmbedder
from rag.filtering import passes_filters
from rag.rerank import Reranker
from rag.store import QdrantStore, build_filter
from rag.types import Candidate, Filters

# Какие поля Filters применимы к какой коллекции (см. контракт: payload
# wineries/knowledge беднее, чем wines — там нет color/sugar/grapes/stillness).
_FILTER_MODE = {"wines": "full", "wineries": "region", "knowledge": "none"}


class HybridSearcher:
    def __init__(
        self,
        store: QdrantStore,
        embedder: DenseEmbedder,
        bm25_indexes: dict[str, BM25Index],
        payload_by_id: dict[str, dict[str, dict]],
        reranker: Reranker,
    ):
        self.store = store
        self.embedder = embedder
        self.bm25_indexes = bm25_indexes
        self.payload_by_id = payload_by_id
        self.reranker = reranker

    # ------------------------------------------------------------------ #
    def _dense_hits(self, query: str, filters: Filters | None, collections: tuple[str, ...], pool: int):
        vector = self.embedder.embed_query(query)
        hits = []
        for coll in collections:
            mode = _FILTER_MODE.get(coll, "none")
            qfilter = None
            if mode == "full":
                qfilter = build_filter(filters, include_region=True, include_wine_fields=True)
            elif mode == "region":
                qfilter = build_filter(filters, include_region=True, include_wine_fields=False)
            for gid, score, payload in self.store.search(coll, vector, top_k=pool, query_filter=qfilter):
                hits.append((gid, score, payload))
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:pool]

    def _bm25_hits(self, query: str, filters: Filters | None, collections: tuple[str, ...], pool: int):
        hits = []
        for coll in collections:
            bm25 = self.bm25_indexes.get(coll)
            if bm25 is None:
                continue
            mode = _FILTER_MODE.get(coll, "none")
            by_id = self.payload_by_id.get(coll, {})
            for doc_id, score in bm25.scores(query).items():
                payload = by_id.get(doc_id)
                if payload is None:
                    continue
                if mode == "full" and not passes_filters(payload, filters, include_wine_fields=True):
                    continue
                if mode == "region" and not passes_filters(payload, filters, include_wine_fields=False):
                    continue
                hits.append((doc_id, score, payload))
        hits.sort(key=lambda x: x[1], reverse=True)
        return hits[:pool]

    @staticmethod
    def _rrf_fuse(dense_hits, bm25_hits, k: int) -> list[str]:
        dense_rank = {gid: i for i, (gid, _s, _p) in enumerate(dense_hits)}
        bm25_rank = {gid: i for i, (gid, _s, _p) in enumerate(bm25_hits)}
        all_ids = set(dense_rank) | set(bm25_rank)
        rrf = {}
        for gid in all_ids:
            s = 0.0
            if gid in dense_rank:
                s += 1.0 / (k + dense_rank[gid] + 1)
            if gid in bm25_rank:
                s += 1.0 / (k + bm25_rank[gid] + 1)
            rrf[gid] = s
        return sorted(all_ids, key=lambda gid: rrf[gid], reverse=True)

    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        filters: Filters | None = None,
        collections: tuple[str, ...] = ("wines", "knowledge"),
        top_k: int = 8,
        use_reranker: bool = True,
    ) -> list[Candidate]:
        pool = config.CANDIDATE_POOL
        dense_hits = self._dense_hits(query, filters, collections, pool)
        bm25_hits = self._bm25_hits(query, filters, collections, pool)

        payload_lookup: dict[str, dict] = {}
        for gid, _s, payload in dense_hits:
            payload_lookup[gid] = payload
        for gid, _s, payload in bm25_hits:
            payload_lookup.setdefault(gid, payload)

        ranked_ids = self._rrf_fuse(dense_hits, bm25_hits, config.RRF_K)[:pool]
        if not ranked_ids:
            return []

        docs = [payload_lookup[gid].get("text", "") for gid in ranked_ids]
        if use_reranker:
            scores = self.reranker.rerank(query, docs)
        else:
            n = len(docs)
            scores = [float(n - i) for i in range(n)]

        order = sorted(range(len(ranked_ids)), key=lambda i: scores[i], reverse=True)[:top_k]
        results = []
        for i in order:
            gid = ranked_ids[i]
            payload = payload_lookup[gid]
            results.append(
                Candidate(
                    id=gid,
                    kind=payload.get("kind", "wine"),
                    score=round(float(scores[i]), 4),
                    text=payload.get("text", ""),
                    url=payload.get("url", ""),
                    meta=payload,
                )
            )
        return results

    def similar(self, wine_id: str, top_k: int = 6) -> list[Candidate]:
        vector = self.store.get_vector("wines", wine_id)
        if vector is None:
            return []
        hits = self.store.search("wines", vector, top_k=top_k + 1)
        results = []
        for gid, score, payload in hits:
            if gid == wine_id:
                continue
            results.append(
                Candidate(
                    id=gid,
                    kind=payload.get("kind", "wine"),
                    score=round(float(score), 4),
                    text=payload.get("text", ""),
                    url=payload.get("url", ""),
                    meta=payload,
                )
            )
            if len(results) >= top_k:
                break
        return results
