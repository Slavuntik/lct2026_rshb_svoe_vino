import pandas as pd
import pytest

from winescan.eval.train_fusion import evaluate, group_queries, open_set, split_queries
from winescan.search.fusion import FEATURES, FusionModel


def _frame() -> pd.DataFrame:
    rows = []
    for i in range(10):
        for slug, label, visual, color in (("right", True, 0.80, 0.05), ("wrong", False, 0.81, 0.40)):
            features = {name: 0.0 for name in FEATURES} | {"visual": visual, "color_distance": color}
            rows.append({"query_id": f"q{i}", "slug": slug, "label": label, "inliers": 10,
                         "in_phash_group": i % 2 == 0, "shares_image": False, **features})  # fmt: skip
    return pd.DataFrame(rows)


def test_group_and_split_queries():
    queries = group_queries(_frame())
    fit, check = split_queries(list(queries))

    assert len(queries) == 10 and len(queries["q0"]) == 2
    assert sorted(fit + check) == sorted(queries) and not set(fit) & set(check)


def test_evaluate_visual_vs_color_scorer():
    queries = group_queries(_frame())
    ids = list(queries)

    assert evaluate(queries, ids, lambda c: c["features"]["visual"])["all"] == 0.0
    result = evaluate(queries, ids, lambda c: -c["features"]["color_distance"])
    assert result["all"] == 1.0 and result["in_phash_group"] == 1.0 and result["shares_image"] is None


def test_open_set_uses_candidates_without_true_wine_as_negatives():
    queries = group_queries(_frame())
    model = FusionModel({"color_distance": -1.0}, 0.0, {}, {})

    result = open_set(model, queries, list(queries), threshold=-0.2)

    # позитивы: лучший — верный с логитом −0,05 ≥ −0,2; негативы: остаётся «wrong» с −0,40 < −0,2
    assert result["open_set_accuracy"] == pytest.approx(1.0)
    assert result["auroc"] == pytest.approx(1.0)
