"""Eval-раннер считает метрики на мини-фикстуре из 5 вопросов.

Корпус нарочно крошечный (7 вин + 1 winery + 1 знание), поэтому top_k=8
возвращает практически весь пул кандидатов — это специально: цель теста не
качество ранжирования (это работа настоящего голд-сета eval/goldset.jsonl на
полном каталоге), а корректность АРИФМЕТИКИ раннера (hit@k/MRR), включая
гарантированный промах на несуществующем id.
"""
from __future__ import annotations

import pytest

from rag.eval import evaluate

GOLDSET = [
    {
        "q": "Хочу Просекко, посоветуйте аналог",
        "type": "analog",
        "expect_ids": ["sparkling-white-prosecco-1"],
        "expect_any": True,
    },
    {
        "q": "Танины в вине что это такое",
        "type": "fact",
        # id совпадает с форматом Candidate.id для knowledge: "article:<article_id>#<n>"
        "expect_ids": ["article:test-article-fact#0"],
        "expect_any": True,
    },
    {
        "q": "белое сухое вино к рыбе",
        "type": "pick",
        "expect_ids": ["white-dry-fish-1", "white-dry-fish-2"],
        "expect_any": True,
    },
    {
        "q": "вино к стейку",
        "type": "pairing",
        "expect_ids": ["red-dry-kuban-1", "red-dry-kuban-2"],
        "expect_any": True,
    },
    {
        # гарантированный промах: такого id нет и не может быть в корпусе
        "q": "запрос про единорогов и радугу",
        "type": "pick",
        "expect_ids": ["nonexistent-wine-id-xyz"],
        "expect_any": True,
    },
]


def test_evaluate_computes_expected_metrics(tiny_index):
    report = evaluate(tiny_index, GOLDSET, top_k=8)

    assert report["n"] == 5
    assert report["top_k"] == 8
    assert set(report["by_type"].keys()) == {"analog", "fact", "pick", "pairing"}
    assert report["by_type"]["pick"]["n"] == 2  # 2 вопроса типа pick в мини-голдсете

    # 4 гарантированных попадания (корпус трижды меньше top_k=8 — весь пул
    # возвращается) + 1 гарантированный промах на несуществующем id.
    assert report["hit_at_8"] == pytest.approx(0.8)
    assert 0.0 < report["mrr"] <= 1.0

    # деталь по промаху — вручную проверяем, что раннер не сжульничал
    miss = next(d for d in report["details"] if "единорог" in d["q"])
    assert miss["hit"] == 0
    assert miss["rr"] == 0.0

    hit_example = next(d for d in report["details"] if d["type"] == "analog")
    assert hit_example["hit"] == 1
    assert hit_example["rr"] == pytest.approx(1.0)  # единственный prosecco в фикстуре — ранг 1


def test_evaluate_handles_empty_goldset(tiny_index):
    report = evaluate(tiny_index, [], top_k=8)
    assert report["n"] == 0
    assert report["hit_at_8"] == 0.0
    assert report["mrr"] == 0.0
    assert report["by_type"] == {}
