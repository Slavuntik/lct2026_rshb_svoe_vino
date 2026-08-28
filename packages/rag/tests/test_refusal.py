"""Отсечка-refusal (контракт v0.3, ревью 02, блокер-риск 2): search()
возвращает [] когда top-скор ПОСЛЕ реранка ниже откалиброванного порога —
иначе dense-ветка всегда что-то находит, и LLM цитирует чушь на посторонние
вопросы («как починить карбюратор»).

Два уровня: сам гейт в HybridSearcher.search (с фиктивным реранкером
контролируемого скора — не тянем реальный кросс-энкодер) и арифметика
калибровки в rag.calibrate (с фиктивным hybrid — не тянем реальный индекс).
"""
from __future__ import annotations

from dataclasses import dataclass

from rag.calibrate import calibrate_refusal_threshold
from rag.hybrid import HybridSearcher


class _FixedScoreReranker:
    """Возвращает по убыванию скоры вокруг заданного значения — управляемая
    замена кросс-энкодеру для проверки порога независимо от реальной модели."""

    def __init__(self, score: float):
        self.score = score

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        return [self.score - i * 0.001 for i in range(len(docs))]


def _hybrid_with(tiny_index, *, score: float, threshold: float | None):
    return HybridSearcher(
        store=tiny_index.store,
        embedder=tiny_index.embedder,
        bm25_indexes=tiny_index.bm25_indexes,
        payload_by_id=tiny_index.payload_by_id,
        reranker=_FixedScoreReranker(score),
        refusal_threshold=threshold,
    )


def test_refusal_blocks_when_top_score_below_threshold(tiny_index):
    hybrid = _hybrid_with(tiny_index, score=0.1, threshold=1.0)
    assert hybrid.search("вино к рыбе", collections=("wines",), top_k=8) == []


def test_no_refusal_when_top_score_above_threshold(tiny_index):
    hybrid = _hybrid_with(tiny_index, score=5.0, threshold=1.0)
    assert hybrid.search("вино к рыбе", collections=("wines",), top_k=8) != []


def test_refusal_disabled_when_threshold_is_none(tiny_index):
    hybrid = _hybrid_with(tiny_index, score=-100.0, threshold=None)
    assert hybrid.search("вино к рыбе", collections=("wines",), top_k=8) != []


def test_apply_refusal_false_bypasses_gate_for_calibration(tiny_index):
    # Калибровке (rag/calibrate.py) нужен СЫРОЙ скор до того, как порог
    # вообще существует — apply_refusal=False должен игнорировать гейт.
    hybrid = _hybrid_with(tiny_index, score=-100.0, threshold=1.0)
    results = hybrid.search("вино к рыбе", collections=("wines",), top_k=8, apply_refusal=False)
    assert results != []
    assert results[0].score < 1.0  # сырой (низкий) скор реально дошёл до вызывающего


def test_refusal_not_applied_without_reranker(tiny_index):
    # Порог откалиброван под шкалу кросс-энкодера; без реранка (RRF-скор,
    # другая шкала) применять его бессмысленно — он бы резал всегда.
    hybrid = _hybrid_with(tiny_index, score=-100.0, threshold=1.0)
    results = hybrid.search("вино к рыбе", collections=("wines",), top_k=8, use_reranker=False)
    assert results != []


def test_pairing_route_ignores_global_refusal_threshold_on_sufficient_wines(tiny_index, monkeypatch):
    """Регресс, найденный на реальном демо-вопросе («Что взять к сырам?»,
    приёмка F): порог refusal калиброван на смешанном пуле wines+knowledge,
    где статьи систематически скорят выше терпимых карточек вина — тот же
    эффект, из-за которого вообще понадобился pairing-роутинг. Из-за этого
    top-скор ЧИСТО wines для честного гастро-вопроса может провалиться ниже
    порога, хотя вопрос совершенно легитимен. Retriever._search_pairing не
    должен в этом случае откатываться на knowledge (что вернуло бы статьи —
    ровно то, чего просила избежать приёмка F) — если карточек вин хватило
    на top_k, порог для них не проверяется вовсе (сама классификация
    "pairing" — достаточный сигнал "в теме")."""
    low_score_reranker = _FixedScoreReranker(score=-5.0)  # заведомо ниже любого разумного порога
    monkeypatch.setattr(tiny_index.hybrid, "reranker", low_score_reranker)
    monkeypatch.setattr(tiny_index.hybrid, "refusal_threshold", 0.0)

    results = tiny_index.search("Что взять к сырам?", top_k=5)  # top_k=5: в фикстуре 7 вин, гарантированно "достаточно"
    assert results, "wines-приоритет не должен схлопнуться в пустоту из-за глобального порога"
    for c in results:
        assert c.kind == "wine", "не должно откатываться на knowledge, если вин хватило"


def test_refusal_empty_candidate_pool_still_returns_empty(tiny_index):
    hybrid = _hybrid_with(tiny_index, score=5.0, threshold=1.0)
    # запрос, для которого пул кандидатов и так пуст (жёсткий фильтр всё отсекает)
    from rag.types import Filters

    results = hybrid.search(
        "вино", filters=Filters(color="белое", region="dolina-dona"), collections=("wines",), top_k=8
    )
    assert results == []


# --------------------------------------------------------------------------
# Калибровка (rag/calibrate.py) — арифметика на фиктивном hybrid, без индекса
# --------------------------------------------------------------------------


@dataclass
class _FakeCandidate:
    score: float


class _FakeHybrid:
    """Подменяет HybridSearcher.search для юнит-теста арифметики калибровки —
    без реального Qdrant/BM25/эмбеддингов."""

    def __init__(self, score_by_query: dict[str, float]):
        self.score_by_query = score_by_query

    def search(self, q, *, collections, top_k, use_reranker, apply_refusal):
        score = self.score_by_query.get(q)
        if score is None:
            return []
        return [_FakeCandidate(score=score)]


def test_calibrate_clean_separation_picks_midpoint():
    goldset = [
        {"q": "легитимный вопрос 1", "type": "pick"},
        {"q": "легитимный вопрос 2", "type": "fact"},
        {"q": "мусорный вопрос 1", "type": "refusal"},
        {"q": "мусорный вопрос 2", "type": "refusal"},
    ]
    fake = _FakeHybrid(
        {
            "легитимный вопрос 1": 3.0,
            "легитимный вопрос 2": 2.5,
            "мусорный вопрос 1": 0.1,
            "мусорный вопрос 2": 0.3,
        }
    )
    result = calibrate_refusal_threshold(fake, goldset, top_k=8)
    assert result["threshold"] is not None
    assert 0.3 < result["threshold"] < 2.5
    assert result["overlap"] is False


def test_calibrate_skips_analog_type():
    goldset = [
        {"q": "аналог-вопрос", "type": "analog"},  # не должен звать fake.search вовсе
        {"q": "легит", "type": "pick"},
        {"q": "мусор", "type": "refusal"},
    ]
    fake = _FakeHybrid({"легит": 2.0, "мусор": 0.1, "аналог-вопрос": 999.0})
    result = calibrate_refusal_threshold(fake, goldset, top_k=8)
    # если бы analog не пропускался, 999.0 испортил бы "legit_min" — порог был бы иным
    assert result["legit_min"] == 2.0


def test_calibrate_insufficient_data_returns_none_threshold():
    goldset = [{"q": "легит", "type": "pick"}]  # ни одного refusal-вопроса
    fake = _FakeHybrid({"легит": 2.0})
    result = calibrate_refusal_threshold(fake, goldset, top_k=8)
    assert result["threshold"] is None
    assert result["reason"] == "not_enough_calibration_data"


def test_calibrate_overlap_uses_conservative_percentile():
    # Перекрытие: часть мусора скорит выше слабого легита.
    goldset = [
        {"q": "легит слабый", "type": "pick"},
        {"q": "легит сильный", "type": "fact"},
        {"q": "мусор слабый", "type": "refusal"},
        {"q": "мусор сильный", "type": "refusal"},
    ]
    fake = _FakeHybrid(
        {"легит слабый": 1.0, "легит сильный": 3.0, "мусор слабый": 0.1, "мусор сильный": 1.5}
    )
    result = calibrate_refusal_threshold(fake, goldset, top_k=8)
    assert result["overlap"] is True
    assert result["threshold"] is not None
