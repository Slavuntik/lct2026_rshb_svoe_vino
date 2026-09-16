"""Тесты cv/audit.py — дешёвый (без энкодера/Qdrant) диагностический проход детектора
этикетки по эталонам. G3, дополнение оркестратора от 16.09.2026: "среди эталонов кейса
много шума — лайфстайл-фото, виноградники, интерьеры вместо снимка бутылки"; детектор
силуэта (cv.normalize._foreground_bbox), не находящий контрастную "бутылку на фоне",
откатывается на fallback-кроп всего кадра — дешёвый прокси-сигнал шума, не классификатор
(F3 делает настоящий семантический триаж отдельно)."""
from __future__ import annotations

import numpy as np

from cv import imageio
from cv.audit import label_detector_outcomes
from cv.normalize import detector_used_fallback


def test_detector_used_fallback_false_on_bottle_like_image(synthetic_bottle_image):
    """Тёмное "стекло" + контрастная светлая "этикетка" в нижних 2/3 — структурная
    форма, которую детектор силуэта уверенно находит (см. conftest.py)."""
    assert detector_used_fallback(synthetic_bottle_image) is False


def test_detector_used_fallback_true_on_uniform_image():
    """Совершенно однородный кадр — нет контраста с фоном рамки, связной области не
    находится -> откат на fallback. Прокси для лайфстайл/интерьерного кадра без
    выраженного силуэта бутылки на нейтральном/полочном фоне."""
    uniform = np.full((200, 200, 3), 128, dtype=np.uint8)
    assert detector_used_fallback(uniform) is True


def test_label_detector_outcomes_aggregates_and_flags_unreadable(tmp_path, synthetic_bottle_image):
    good_path = tmp_path / "good.jpg"
    good_path.write_bytes(imageio.encode_jpeg(synthetic_bottle_image))

    bad_path = tmp_path / "bad.jpg"
    bad_path.write_bytes(imageio.encode_jpeg(np.full((200, 200, 3), 128, dtype=np.uint8)))

    broken_path = tmp_path / "broken.jpg"
    broken_path.write_bytes(b"not an image, just garbage bytes")

    report = label_detector_outcomes(
        {"slug-good": good_path, "slug-bad": bad_path, "slug-broken": broken_path}, verbose=False
    )

    assert report["total"] == 2  # broken не декодируется -> не входит в total/per_slug
    assert report["unreadable_slugs"] == ["slug-broken"]
    assert report["per_slug"] == {"slug-good": False, "slug-bad": True}
    assert report["fallback_slugs"] == ["slug-bad"]
    assert report["fallback_count"] == 1
    assert report["fallback_rate"] == 0.5


def test_label_detector_outcomes_empty_input():
    report = label_detector_outcomes({}, verbose=False)
    assert report == {
        "total": 0,
        "fallback_count": 0,
        "fallback_rate": 0.0,
        "fallback_slugs": [],
        "unreadable_slugs": [],
        "per_slug": {},
    }
