"""agents/ML-2-shelf-crop.md — `CV_SHELF_CROP`/`CV_SHELF_MIN_BOXES`/
`CV_SHELF_CHECK_NEIGHBORS` в `apps/api/app/cv/service.py::run_photo_scan`
(`_apply_shelf_crop`, `_pick_best_shelf_crop`). Реального RapidOCR не касаемся —
`cv.shelf_crop.segment_shelf` подменяется НАПРЯМУЮ на модуле (тот же приём, что
`test_scan_photo_fusion.py` применяет к `_fusion_text_index`/... через `app.cv.
service`, только здесь патчим саму лениво импортируемую функцию пакета
`packages/cv`, не lru_cache-обёртку apps/api).

Флаг включён -> `_apply_shelf_crop` реально декодирует байты (`cv.imageio.
decode_image`) — фикстуры этого файла ВСЕГДА настоящий JPEG (`_make_jpeg`), не
`b"MOCKPHOTO:..."` сентинел, который остальные тесты `test_scan_photo*.py`
используют (тот сентинел не декодируется как изображение вовсе — ровно так
тест "флаг выключен" ниже ДОКАЗЫВАЕТ, что `decode_image` в этом случае не
вызывается: сентинел прошёл бы мимо без ValueError).
"""
from __future__ import annotations

import dataclasses
import io

from PIL import Image
from starlette.testclient import TestClient

from app.cv.interface import Match
from tests.test_scan_photo import _photo


def _make_jpeg(w: int, h: int, color: tuple[int, int, int] = (120, 40, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _RecordingImageIndex:
    """`.search()` запоминает КАЖДОЕ полученное изображение (декодированную
    ширину/высоту — удобнее байт для сравнения "применился ли кроп"). Скор —
    по ПОРЯДКУ вызова (`scores[i]` для i-го вызова, `default_score` дальше) —
    не по содержимому байт: `CV_SHELF_CHECK_NEIGHBORS`-тест ниже различает
    кандидатов по порядку зондирования (`_pick_best_shelf_crop` пробует их
    строго по `candidate_indices`, см. `app/cv/service.py`), не по пикселям —
    надёжнее, чем полагаться на то, что JPEG-перекодирование однотонных
    кропов даст побитово различимые цвета."""

    index_version = "shelf-crop-stub"

    def __init__(self, scores: list[float] | None = None, default_score: float = 0.9):
        self.received_sizes: list[tuple[int, int]] = []
        self.received_bytes: list[bytes] = []
        self.search_calls = 0
        self._scores = scores
        self._default_score = default_score

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        self.received_bytes.append(image)
        try:
            with Image.open(io.BytesIO(image)) as im:
                size = im.size
        except Exception:  # noqa: BLE001 — тесты "флаг выключен" шлют недекодируемый сентинел нарочно
            size = None
        self.received_sizes.append(size)
        if self._scores is not None and self.search_calls < len(self._scores):
            score = self._scores[self.search_calls]
        else:
            score = self._default_score
        self.search_calls += 1
        return [Match(slug="shato-vymysel-cabernet", score=score, gap=0.5, view="real")]

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def _fake_segmentation(*, is_shelf: bool, crops, center_index: int = 0, candidate_indices=None):
    from cv.shelf_crop import ShelfSegmentation

    return ShelfSegmentation(
        crops=tuple(crops), center_index=center_index, is_shelf=is_shelf,
        n_row_boxes=42, col_method="ocr" if is_shelf else "none", n_boxes=42,
        candidate_indices=tuple(candidate_indices) if candidate_indices is not None else (center_index,),
    )


# --------------------------------------------------------------------------------------
# Флаг выключен (дефолт) — регрессия невозможна by construction: сегментация вообще
# не запускается (доказано сентинел-байтами, которые не декодируются как изображение).
# --------------------------------------------------------------------------------------


def test_default_settings_have_shelf_crop_disabled():
    from app.config import Settings

    settings = Settings()
    assert settings.cv_shelf_crop is False
    assert settings.cv_shelf_min_boxes == 30
    assert settings.cv_shelf_check_neighbors is False


def test_flag_off_never_touches_image_bytes_at_all(client: TestClient, app):
    """`b"MOCKPHOTO:..."` не декодируется как JPEG — если бы `_apply_shelf_crop`
    хоть раз попыталась декодировать его, `cv.imageio.decode_image` бросила бы
    `ValueError`, и rich-режим ответил бы 400, а не 200. Флаг выключен по
    умолчанию -> `image_index.search()` обязан получить РОВНО исходные байты."""
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    assert app.state.settings.cv_shelf_crop is False

    payload = b"MOCKPHOTO:shato-vymysel-cabernet"
    r = _photo(client, payload, flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 1
    assert idx.received_bytes[0] == payload


def test_flag_off_flat_mode_also_unaffected(client: TestClient, app):
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    payload = b"MOCKPHOTO:shato-vymysel-cabernet"
    r = _photo(client, payload, flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}
    assert idx.received_bytes[0] == payload


# --------------------------------------------------------------------------------------
# Флаг включён, гейт «это полка» НЕ пройден — байты дальше идут ПОБИТОВО, без
# единого перекодирования (regression на не-полочных/студийных фото невозможна).
# --------------------------------------------------------------------------------------


def test_flag_on_but_not_a_shelf_keeps_bytes_byte_for_byte(client: TestClient, app, monkeypatch):
    import cv.shelf_crop as shelf_crop_module

    monkeypatch.setattr(
        shelf_crop_module, "segment_shelf",
        lambda arr, **kw: _fake_segmentation(is_shelf=False, crops=[(0, 0, arr.shape[1], arr.shape[0])]),
    )
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True)

    payload = _make_jpeg(400, 900)
    r = _photo(client, payload, flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 1
    assert idx.received_bytes[0] == payload  # ПОБИТОВО исходные байты, не перекодированные заново


# --------------------------------------------------------------------------------------
# Флаг включён, гейт пройден — центральный кроп применяется и уходит дальше по конвейеру.
# --------------------------------------------------------------------------------------


def test_flag_on_with_shelf_fixture_applies_central_crop(client: TestClient, app, monkeypatch):
    import cv.shelf_crop as shelf_crop_module

    def _fake_segment_shelf(arr, **kw):
        h, w = arr.shape[:2]
        return _fake_segmentation(
            is_shelf=True, crops=[(0, 0, w // 2, h), (w // 2, 0, w, h)], center_index=1,
        )

    monkeypatch.setattr(shelf_crop_module, "segment_shelf", _fake_segment_shelf)
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True)

    payload = _make_jpeg(400, 900)
    r = _photo(client, payload, flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 1
    assert idx.received_bytes[0] != payload  # перекодировано — не исходные байты
    assert idx.received_sizes[0] == (200, 900)  # ровно правая половина (center_index=1)


def test_flag_on_min_boxes_override_reaches_segment_shelf(client: TestClient, app, monkeypatch):
    import cv.shelf_crop as shelf_crop_module

    captured = {}

    def _fake_segment_shelf(arr, min_boxes=30, **kw):
        captured["min_boxes"] = min_boxes
        h, w = arr.shape[:2]
        return _fake_segmentation(is_shelf=False, crops=[(0, 0, w, h)])

    monkeypatch.setattr(shelf_crop_module, "segment_shelf", _fake_segment_shelf)
    app.state.image_index = _RecordingImageIndex()
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True, cv_shelf_min_boxes=17)

    r = _photo(client, _make_jpeg(200, 200), flat=False)
    assert r.status_code == 200, r.text
    assert captured["min_boxes"] == 17


def test_flag_on_applies_to_flat_mode_too(client: TestClient, app, monkeypatch):
    """`/scan/photo?flat=1` зовёт ту же `run_photo_scan()` — сегментация не
    привязана к rich-режиму (контракт: правка ОДНА на все пути, docstring
    apps/api/app/routers/scan.py)."""
    import cv.shelf_crop as shelf_crop_module

    def _fake_segment_shelf(arr, **kw):
        h, w = arr.shape[:2]
        return _fake_segmentation(is_shelf=True, crops=[(0, 0, w // 2, h), (w // 2, 0, w, h)], center_index=0)

    monkeypatch.setattr(shelf_crop_module, "segment_shelf", _fake_segment_shelf)
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True)

    r = _photo(client, _make_jpeg(400, 900), flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}
    assert idx.received_sizes[0] == (200, 900)  # левая половина (center_index=0)


# --------------------------------------------------------------------------------------
# CV_SHELF_CHECK_NEIGHBORS (доп. пункт брифа) — независимый флаг, только когда
# `candidate_indices` реально несёт > 1 кандидата.
# --------------------------------------------------------------------------------------


def test_check_neighbors_disabled_by_default_uses_center_crop_only(client: TestClient, app, monkeypatch):
    import cv.shelf_crop as shelf_crop_module

    def _fake_segment_shelf(arr, **kw):
        h, w = arr.shape[:2]
        third = w // 3
        return _fake_segmentation(
            is_shelf=True, crops=[(0, 0, third, h), (third, 0, 2 * third, h), (2 * third, 0, w, h)],
            center_index=1, candidate_indices=(1, 2),  # пограничный случай ЕСТЬ, но флаг выключен
        )

    monkeypatch.setattr(shelf_crop_module, "segment_shelf", _fake_segment_shelf)
    idx = _RecordingImageIndex()
    app.state.image_index = idx
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True)
    assert app.state.settings.cv_shelf_check_neighbors is False

    r = _photo(client, _make_jpeg(300, 90), flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 1  # НЕ пробует соседей — ровно один поиск
    assert idx.received_sizes[0] == (100, 90)  # центральная (index 1), без сравнения с соседом


def test_check_neighbors_enabled_picks_higher_scoring_candidate(client: TestClient, app, monkeypatch):
    """Гейт пограничный (`candidate_indices` несёт центр И соседа, разной
    ширины — чтобы отличить их по размеру) — обе стороны пробуются через
    `ImageIndex.search()` СТРОГО по порядку `candidate_indices=(1,2)` (центр,
    затем сосед), выигрывает более высокий CV-скор (здесь — второй
    зондированный, сосед), и ИМЕННО его размер уходит в финальный поиск
    конвейера (третий вызов `.search()`)."""
    import cv.shelf_crop as shelf_crop_module

    # 3 колонки разной ширины: far(0-60), center(60-150, w=90), neighbor(150-300, w=150)
    def _fake_segment_shelf(arr, **kw):
        h, w = arr.shape[:2]
        return _fake_segmentation(
            is_shelf=True, crops=[(0, 0, 60, h), (60, 0, 150, h), (150, 0, w, h)],
            center_index=1, candidate_indices=(1, 2),
        )

    monkeypatch.setattr(shelf_crop_module, "segment_shelf", _fake_segment_shelf)
    # 1-й зонд (центр, w=90) -> 0.5; 2-й зонд (сосед, w=150) -> 0.95, побеждает
    idx = _RecordingImageIndex(scores=[0.5, 0.95])
    app.state.image_index = idx
    app.state.settings = dataclasses.replace(app.state.settings, cv_shelf_crop=True, cv_shelf_check_neighbors=True)

    r = _photo(client, _make_jpeg(300, 90), flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 3  # 2 кандидата (center+neighbor) + 1 финальный поиск конвейера
    assert idx.received_sizes[0] == (90, 90)  # 1-й зонд — центр
    assert idx.received_sizes[1] == (150, 90)  # 2-й зонд — сосед
    assert idx.received_sizes[2] == (150, 90)  # финальный поиск конвейера — ПОБЕДИВШИЙ (сосед)
    # финальный (3-й) вызов — уже ЗА пределами списка `scores` -> дефолтный скор (0.9), не 0.95
    # зонда: top1_score в ответе — результат ОТДЕЛЬНОГО, финального поиска на выбранном кропе,
    # не запомненный скор зонда сравнения.
    assert r.json()["confidence"]["top1_score"] == 0.9


def test_check_neighbors_single_candidate_does_not_probe_extra():
    """`candidate_indices` длиной 1 (не пограничный случай) -> флаг включён, но
    сравнивать нечего — ровно один поиск, тот же путь, что и без флага."""
    from app.cv.service import _apply_shelf_crop
    from app.config import Settings

    class _OneCallIndex(_RecordingImageIndex):
        pass

    import cv.shelf_crop as shelf_crop_module

    def _fake_segment_shelf(arr, **kw):
        h, w = arr.shape[:2]
        return _fake_segmentation(is_shelf=True, crops=[(0, 0, w, h)], center_index=0, candidate_indices=(0,))

    orig = shelf_crop_module.segment_shelf
    shelf_crop_module.segment_shelf = _fake_segment_shelf
    try:
        idx = _OneCallIndex()
        settings = dataclasses.replace(Settings(), cv_shelf_crop=True, cv_shelf_check_neighbors=True)
        out = _apply_shelf_crop(_make_jpeg(200, 200), settings, idx)
    finally:
        shelf_crop_module.segment_shelf = orig
    assert idx.search_calls == 0  # единственный кандидат -> _pick_best_shelf_crop не вызывается вовсе
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (200, 200)  # кроп (весь кадр per fake) всё равно применяется и перекодируется
