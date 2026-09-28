from rag.hybrid import HybridSearcher
from rag import config


def test_large_request_preserves_tail_and_bounded_rerank(monkeypatch):
    monkeypatch.setattr(config, "CANDIDATE_POOL", 5)
    monkeypatch.setattr(config, "RERANK_POOL", 2)
    class Reranker:
        def rerank(self, query, docs):
            assert len(docs) <= 2
            return [2.0, 1.0][:len(docs)]
    searcher = HybridSearcher.__new__(HybridSearcher)
    searcher.reranker = Reranker()
    searcher.refusal_threshold = None
    def hits(query, filters, collections, pool):
        return [(f"wine-{i}", 10-i, {"kind":"wine", "text":"wine", "source":{}, "derived":{}}) for i in range(min(pool, 12))]
    monkeypatch.setattr(searcher, "_dense_hits", hits)
    monkeypatch.setattr(searcher, "_bm25_hits", hits)
    result = searcher.search("вино", collections=("wines",), top_k=12)
    assert len(result) == 12
    assert result[-1].id == "wine-11"
    assert len(searcher.search("вино", collections=("wines",), top_k=3)) == 3
