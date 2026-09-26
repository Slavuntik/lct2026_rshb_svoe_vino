import importlib
from pathlib import Path


def test_proposals_reject_unknown_ids_duplicates_and_missing_evidence(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    proposals = importlib.import_module("evaluate_vlm").proposals
    row = {
        "file": "x.jpg",
        "seconds": 1,
        "crops": [
            {"crop_id": i, "box": [0, 0, 1, 1], "candidates": ["wine"]}
            for i in range(1, 6)
        ],
        "parsed": {
            "bottles": [
                {"crop_id": 1, "selected_id": "wine", "evidence": "readable variety"},
                {"crop_id": 2, "selected_id": "invented", "evidence": "brand"},
                {"crop_id": 3, "selected_id": "wine", "evidence": ""},
                {"crop_id": 4, "selected_id": "wine", "evidence": "text"},
                {"crop_id": 4, "selected_id": None},
                {"crop_id": 5, "selected_id": None},
            ]
        },
    }
    prediction, errors = proposals(row)
    assert len(prediction["matches"]) == 1
    assert prediction["matches"][0]["wineId"] == "wine"
    assert errors.count("unknown_or_duplicate_crop") == 2
    assert "out_of_shortlist" in errors
    assert "missing_evidence" in errors
