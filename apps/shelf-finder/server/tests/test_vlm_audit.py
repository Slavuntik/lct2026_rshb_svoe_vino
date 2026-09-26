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


def test_incomplete_reference_run_cannot_be_reported_as_complete(monkeypatch):
    import pytest

    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    report = importlib.import_module("evaluate_vlm").report
    with pytest.raises(ValueError, match="unfinished"):
        report({}, {"rows": [{"complete": False}]}, {})


def test_qwen_cannot_replace_native_match_or_bypass_geometric_thresholds(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    verify = importlib.import_module("verify_vlm").verified_by_geometry
    strong = [
        {
            "id": "candidate",
            "inliers": 30,
            "labelInliers": 25,
            "matches": 40,
            "coverage": 0.1,
        },
        {
            "id": "rival",
            "inliers": 10,
            "labelInliers": 10,
            "matches": 30,
            "coverage": 0.1,
        },
    ]
    assert verify("candidate", {"id": "other", "rescueEvidence": strong}) is None
    assert (
        verify("candidate", {"id": None, "rescueEvidence": strong}) == "strong_geometry"
    )
    assert verify("candidate", {"id": None}) is None
    assert verify("candidate", {"id": "candidate"}) == "native_accepted"
    weak = [{**strong[0], "labelInliers": 5}, strong[1]]
    assert verify("candidate", {"id": None, "rescueEvidence": weak}) is None


def test_geometric_filter_preserves_raw_output_and_catalog_ambiguity(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    verify = importlib.import_module("verify_vlm").verify
    box = [0, 0, 1, 1]
    raw = {
        "mode": "guided-references",
        "rows": [
            {
                "file": "x.jpg",
                "seconds": 1,
                "crops": [{"crop_id": 1, "box": box, "candidates": ["wine"]}],
                "parsed": {
                    "bottles": [
                        {"crop_id": 1, "selected_id": "wine", "evidence": "same label"},
                        "invalid",
                    ]
                },
            }
        ],
    }
    native = {
        "rows": [
            {
                "file": "x.jpg",
                "observations": [{"box": box, "id": "wine"}],
                "matches": [
                    {"box": box, "wineId": "wine", "alternativeWineIds": ["twin"]}
                ],
            }
        ]
    }
    result = verify(raw, native)
    assert result["rows"][0]["parsed"]["bottles"][0]["selected_id"] is None
    assert raw["rows"][0]["parsed"]["bottles"][0]["selected_id"] == "wine"
