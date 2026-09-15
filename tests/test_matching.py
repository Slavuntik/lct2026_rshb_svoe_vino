import pandas as pd
import pytest

from winescan.catalog.matching import (
    HOUR,
    Status,
    apply_overrides,
    find_candidates,
    orphan_uploads_near,
    resolve_matches,
)
from winescan.catalog.strapi_names import parse_upload

ZERO, ONES = "0" * 16, "f" * 16


def _uploads(*filenames, variants=()):
    rows = [
        {"filename": f, "key": parse_upload(f).key, "is_format_variant": f in variants}
        for f in filenames
    ]
    return pd.DataFrame(rows)


def _img(sha, phash, width=300, height=1000, transparent=0.3, border_mean=255.0, border_std=0.0):
    return {"sha256": sha, "phash": phash, "width": width, "height": height,
            "transparent_share": transparent, "border_mean": border_mean, "border_std": border_std}  # fmt: skip


def test_find_candidates_png_fallback_and_skips_format_variants():
    uploads = _uploads(
        "Shardone_0000000001.webp",
        "Shardone_0000000002.webp",
        "thumbnail_Shardone_0000000001.webp",
        "0m372_png_0000000003.webp",
        variants={"thumbnail_Shardone_0000000001.webp"},
    )
    photos = pd.Series(["Шардоне.webp", "0m372.png", "Нет такого.webp"])

    assert find_candidates(photos, uploads).tolist() == [
        ["Shardone_0000000001.webp", "Shardone_0000000002.webp"],
        ["0m372_png_0000000003.webp"],
        [],
    ]


@pytest.fixture
def resolved():
    catalog = pd.DataFrame(
        [
            ("anchor", "W", ["anchor"]),
            ("same", "W", ["same_a", "same_b"]),
            ("by-time", "W", ["near", "far"]),
            ("missing", "W", []),
            ("tabiya-1", "T", ["t1_old", "t1_new"]),
            ("tabiya-2", "T", ["t2_old", "t2_new"]),
            ("tabiya-3", "T", ["t3_old", "t3_new"]),
            ("unclear", "X", ["shelf_photo", "packshot"]),
            ("exact", "Y", ["pino_aligote_rkatsiteli_0000000001.webp", "pino_aligoterkatsiteli_0000000002.webp"]),
        ],
        columns=["slug", "winery", "candidates"],
    )
    catalog["photo_name"] = catalog["slug"] + ".webp"
    catalog.loc[catalog["slug"] == "exact", "photo_name"] = "pino-aligoterkatsiteli.webp"
    hours = {
        "anchor": 1000, "same_a": 1000, "same_b": 1000.2, "near": 1000.5, "far": 1100,
        "t1_old": 100, "t2_old": 200, "t3_old": 300, "t1_new": 500, "t2_new": 500.1, "t3_new": 500.2,
        "shelf_photo": 10, "packshot": 20,
        "pino_aligote_rkatsiteli_0000000001.webp": 1, "pino_aligoterkatsiteli_0000000002.webp": 1,
    }  # fmt: skip
    mtimes = {f: h * HOUR for f, h in hours.items()}
    images = {
        "anchor": _img("a", ZERO),
        "same_a": _img("s", ZERO, width=100, height=300),
        "same_b": _img("s", ZERO, width=200, height=600),
        "near": _img("n", ZERO), "far": _img("f", ONES),
        "t1_old": _img("t1o", ZERO), "t1_new": _img("t1n", ONES),
        "t2_old": _img("t2o", ZERO), "t2_new": _img("t2n", ONES),
        "t3_old": _img("t3o", ZERO), "t3_new": _img("t3n", ONES),
        "shelf_photo": _img("sh", ZERO, width=800, height=500, transparent=0.0, border_mean=90.0, border_std=40.0),
        "packshot": _img("pk", ONES, width=250, height=900),
        "pino_aligote_rkatsiteli_0000000001.webp": _img("e1", ZERO),
        "pino_aligoterkatsiteli_0000000002.webp": _img("e2", ONES),
    }  # fmt: skip
    return resolve_matches(catalog, mtimes, images).set_index("slug")


@pytest.mark.parametrize(
    ("slug", "status", "image_file"),
    [
        ("anchor", Status.UNIQUE, "anchor"),
        ("same", Status.SAME_CONTENT, "same_b"),
        ("by-time", Status.UPLOAD_TIME, "near"),
        ("missing", Status.MISSING, None),
        ("tabiya-1", Status.WINERY_BATCH, "t1_new"),
        ("tabiya-3", Status.WINERY_BATCH, "t3_new"),
        ("unclear", Status.AMBIGUOUS, "packshot"),
        ("exact", Status.EXACT_NAME, "pino_aligoterkatsiteli_0000000002.webp"),
    ],
)
def test_resolve_matches(resolved, slug, status, image_file):
    actual_file = resolved.loc[slug, "image_file"]
    assert resolved.loc[slug, "image_status"] == status
    assert pd.isna(actual_file) if image_file is None else actual_file == image_file


def test_resolved_alternatives_list_other_content(resolved):
    assert resolved.loc["by-time", "image_alternatives"] == ["far"]
    assert resolved.loc["same", "image_alternatives"] == []


def test_orphan_uploads_near_sorted_by_time_within_window():
    orphans = pd.DataFrame({"filename": ["far", "near", "nearer"], "mtime": [100 * HOUR, 11 * HOUR, 10.2 * HOUR]})

    assert orphan_uploads_near([10 * HOUR, 50 * HOUR], orphans, max_hours=6) == ["nearer", "near"]
    assert orphan_uploads_near([], orphans) == []


def test_apply_overrides_sets_manual_and_validates():
    matches = pd.DataFrame(
        [{"slug": "a", "image_status": Status.AMBIGUOUS, "image_file": "x", "image_alternatives": ["y"], "image_note": ""}]
    )
    overrides = pd.DataFrame([{"slug": "a", "image_file": "y", "reason": "на фото та же этикетка"}])

    result = apply_overrides(matches, overrides, known_files={"x", "y"})
    assert result.loc[0, ["image_status", "image_file"]].tolist() == [Status.MANUAL, "y"]

    with pytest.raises(ValueError):
        apply_overrides(matches, overrides.assign(image_file="z"), known_files={"x", "y"})
