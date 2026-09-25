import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "evaluate_shelves", Path(__file__).parents[2] / "scripts/evaluate_shelves.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_partial_labels_do_not_turn_unreviewed_predictions_into_false_positives():
    dataset = {
        "photos": [
            {
                "file": "photo.jpg",
                "split": "regression",
                "regions": [
                    {"number": 1, "box": [0, 0, 0.4, 0.8], "productId": "known"},
                    {
                        "number": 2,
                        "box": [0.5, 0, 1, 0.8],
                        "rejectedIds": ["wrong"],
                        "status": "unresolved",
                    },
                ],
            }
        ]
    }
    results = {
        "rows": [
            {
                "file": "photo.jpg",
                "timingsMs": {"processing": 100},
                "matches": [
                    {"box": [0, 0, 0.4, 0.8], "wineId": "known"},
                    {"box": [0, 0, 0.4, 0.8], "wineId": "known"},
                    {"box": [0.5, 0, 1, 0.8], "wineId": "not-reviewed"},
                ],
            }
        ]
    }
    score = evaluation.evaluate(dataset, results)["summary"]["all"]
    assert score["correct"] == 1
    assert score["wrong"] == 0
    assert score["unreviewedAccepted"] == 2
    results["rows"][0]["matches"][-1]["wineId"] = "wrong"
    score = evaluation.evaluate(dataset, results)["summary"]["all"]
    assert score["wrong"] == 1


def test_shared_reference_is_not_counted_as_exact_product_recognition():
    dataset = {
        "photos": [
            {
                "file": "x.jpg",
                "split": "regression",
                "regions": [{"number": 1, "box": [0, 0, 1, 1], "productId": "a"}],
            }
        ]
    }
    result = {
        "rows": [
            {
                "file": "x.jpg",
                "timingsMs": {"processing": 1},
                "matches": [
                    {"box": [0, 0, 1, 1], "wineId": "b", "alternativeWineIds": ["a"]}
                ],
            }
        ]
    }
    score = evaluation.evaluate(dataset, result)["summary"]["all"]
    assert score["correct"] == score["wrong"] == 0
    assert score["ambiguousKnown"] == 1
    assert score["unreviewedAccepted"] == 0
