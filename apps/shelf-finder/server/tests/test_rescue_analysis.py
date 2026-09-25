import importlib
from pathlib import Path


def test_analysis_separates_unscheduled_candidates_from_actual_rejections(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    analyze = importlib.import_module("analyze_rescue").analyze
    boxes = [[0, 0, 0.2, 1], [0.3, 0, 0.5, 1], [0.6, 0, 0.8, 1]]
    regions = [
        {"number": i + 1, "box": box, "productId": str(i)}
        for i, box in enumerate(boxes)
    ]
    data = {
        "photos": [
            {"file": "x.jpg", "regions": regions},
            {"file": "missing.jpg", "regions": []},
        ]
    }
    result = {
        "config": {"rescueLimit": 8},
        "rows": [
            {
                "file": "x.jpg",
                "observations": [
                    {
                        "box": boxes[0],
                        "id": None,
                        "rescueQueueRank": 9,
                        "rescueCandidates": ["0"],
                    },
                    {
                        "box": boxes[1],
                        "id": None,
                        "rescueQueueRank": 1,
                        "rescueCandidates": ["1"],
                    },
                    {
                        "box": boxes[2],
                        "id": None,
                        "rescueQueueRank": 2,
                        "rescueCandidates": ["other"],
                    },
                ],
            }
        ],
    }
    report = analyze(data, result)
    assert report["counts"] == {
        "outside_work_limit": 1,
        "checked_but_rejected": 1,
        "missing_from_strong_candidates": 1,
    }
    assert report["missingPhotos"] == ["missing.jpg"]
