"""Калибровка порога refusal (контракт v0.3, ревью 02, блокер-риск 2).

search() отдаёт [] вместо результатов, если top-скор после реранка ниже
порога — без этого dense-ветка всегда что-то находит, и LLM цитирует чушь
на посторонние вопросы («как починить карбюратор»). Порог калибруется на
голд-сете: должен пропускать нормальные вопросы (pick/pairing/fact/travel)
и резать заведомо посторонние (type="refusal"). Хранится в манифесте индекса.

При отсутствии голд-сета или недостатке одной из групп калибровка честно
не выполняется (threshold=None -> refusal выключен) — так `rag ingest` не
падает на синтетических тестовых фикстурах без голд-сета.
"""
from __future__ import annotations

import json
from pathlib import Path

from rag.hybrid import HybridSearcher
from rag.intent import infer_collections


def load_goldset_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def calibrate_refusal_threshold(hybrid: HybridSearcher, goldset: list[dict], *, top_k: int = 8) -> dict:
    legit_scores: list[float] = []
    refusal_scores: list[float] = []

    for q in goldset:
        qtype = q.get("type")
        if qtype == "analog":
            continue  # analog не проходит через search()/реранкер — калибровать нечего
        collections = infer_collections(q["q"])
        # apply_refusal=False: калибруем ДО того, как порог существует —
        # нужен сырой скор, не урезанный ещё не откалиброванным порогом.
        cands = hybrid.search(q["q"], collections=collections, top_k=top_k, use_reranker=True, apply_refusal=False)
        if not cands:
            continue
        top_score = cands[0].score
        (refusal_scores if qtype == "refusal" else legit_scores).append(top_score)

    if not legit_scores or not refusal_scores:
        return {
            "threshold": None,
            "reason": "not_enough_calibration_data",
            "n_legit": len(legit_scores),
            "n_refusal": len(refusal_scores),
        }

    legit_min = min(legit_scores)
    refusal_max = max(refusal_scores)
    overlap = legit_min <= refusal_max
    if not overlap:
        threshold = (legit_min + refusal_max) / 2.0
    else:
        # Перекрытие: часть посторонних вопросов скорит выше, чем самый
        # слабый легитимный (и наоборот). Простой процентиль мусора здесь
        # неправильно расставляет приоритеты — может резать больше легита,
        # чем нужно (эмпирически на первом прогоне 90-й перцентиль refusal
        # оказался ВЫШЕ 13% легитимных вопросов). Вместо этого ищем порог,
        # МАКСИМИЗИРУЮЩИЙ число верно классифицированных вопросов сразу по
        # обеим группам (легит выше порога + мусор ниже порога) — обычный
        # оптимальный 1D порог между классами, не более того.
        candidates = sorted(set(legit_scores) | set(refusal_scores))
        best_threshold = candidates[0]
        best_correct = -1
        for i in range(len(candidates) - 1):
            t = (candidates[i] + candidates[i + 1]) / 2.0
            correct = sum(1 for s in legit_scores if s >= t) + sum(1 for s in refusal_scores if s < t)
            if correct > best_correct:
                best_correct = correct
                best_threshold = t
        threshold = best_threshold

    n_legit_wrongly_refused = sum(1 for s in legit_scores if s < threshold)
    n_refusal_wrongly_passed = sum(1 for s in refusal_scores if s >= threshold)

    return {
        "threshold": round(float(threshold), 4),
        "legit_min": round(float(legit_min), 4),
        "legit_max": round(float(max(legit_scores)), 4),
        "refusal_min": round(float(min(refusal_scores)), 4),
        "refusal_max": round(float(refusal_max), 4),
        "n_legit": len(legit_scores),
        "n_refusal": len(refusal_scores),
        "n_legit_wrongly_refused": n_legit_wrongly_refused,
        "n_refusal_wrongly_passed": n_refusal_wrongly_passed,
        "overlap": overlap,
    }
