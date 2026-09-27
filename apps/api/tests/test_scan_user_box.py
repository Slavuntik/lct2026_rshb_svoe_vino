"""POST /v1/scan/photo с рамкой пользователя (contracts/image-scan.md v0.4.10).

Проверяется не «мок нашёл вино», а то, что до движка доходят именно вырезанные байты:
MockImageIndex узнаёт фикстуры по текстовому префиксу в байтах, а кроп превращает кадр в
JPEG — значит с рамкой мок и не должен ничего находить. Поэтому движок здесь подменяется
записывающей заглушкой через тот же механизм зависимостей, которым пользуется приложение.
"""
from __future__ import annotations

import io

from PIL import Image
from starlette.testclient import TestClient

from app.cv.interface import Match
from app.deps import get_image_index_dep


class _RecordingIndex:
    """Движок, который запоминает полученные байты и всегда отвечает одним кандидатом."""

    index_version = "recording-1"

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        self.seen.append(image)
        return [Match(slug="shato-vymysel-cabernet", score=0.95, gap=0.2, view="real")]

    def build(self, refs, version) -> None:  # pragma: no cover - контракт шире теста
        raise NotImplementedError

    def add(self, slug, images) -> None:  # pragma: no cover - контракт шире теста
        raise NotImplementedError


def _photo_bytes(size=(400, 800)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (140, 30, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _with_recording_index(app) -> _RecordingIndex:
    index = _RecordingIndex()
    app.dependency_overrides[get_image_index_dep] = lambda: index
    return index


def test_box_crops_the_frame_before_the_engine(app, client: TestClient):
    index = _with_recording_index(app)
    payload = _photo_bytes((400, 800))

    response = client.post(
        "/v1/scan/photo",
        files={"image": ("label.jpg", payload, "image/jpeg")},
        data={"box": "0.25,0.10,0.75,0.60"},
    )

    assert response.status_code == 200, response.text
    assert len(index.seen) == 1
    with Image.open(io.BytesIO(index.seen[0])) as cropped:
        # половина ширины и половина высоты исходного кадра, с точностью до округления
        assert abs(cropped.width - 200) <= 1
        assert abs(cropped.height - 400) <= 1


def test_without_box_bytes_reach_the_engine_untouched(app, client: TestClient):
    """Без рамки поведение прежнее — это важнее удобства: на нём держатся все старые тесты."""
    index = _with_recording_index(app)
    payload = b"MOCKPHOTO:shato-vymysel-cabernet"

    client.post("/v1/scan/photo", files={"image": ("label.jpg", payload, "image/jpeg")})

    assert index.seen == [payload]


def test_rich_mode_rejects_a_broken_box(app, client: TestClient):
    _with_recording_index(app)

    response = client.post(
        "/v1/scan/photo",
        files={"image": ("label.jpg", _photo_bytes(), "image/jpeg")},
        data={"box": "0.1,0.2"},
    )

    assert response.status_code == 400
    assert "рамка" in response.text.lower()


def test_rich_mode_rejects_a_degenerate_box(app, client: TestClient):
    _with_recording_index(app)

    response = client.post(
        "/v1/scan/photo",
        files={"image": ("label.jpg", _photo_bytes(), "image/jpeg")},
        data={"box": "0.5,0.5,0.505,0.9"},
    )

    assert response.status_code == 400


def test_flat_mode_survives_a_broken_box(app, client: TestClient):
    """Несгораемость flat важнее аккуратности ввода: рамка игнорируется, ответ остаётся валидным."""
    index = _with_recording_index(app)

    response = client.post(
        "/v1/scan/photo?flat=1",
        files={"image": ("label.jpg", _photo_bytes(), "image/jpeg")},
        data={"box": "мусор"},
    )

    assert response.status_code == 200
    assert list(response.json().keys()) == ["slug"]
    assert index.seen, "скан должен был пройти по всему кадру, а не отмениться"


def test_flat_mode_applies_a_valid_box(app, client: TestClient):
    index = _with_recording_index(app)

    response = client.post(
        "/v1/scan/photo?flat=1",
        files={"image": ("label.jpg", _photo_bytes((400, 800)), "image/jpeg")},
        data={"box": "0.0,0.0,0.5,0.5"},
    )

    assert response.status_code == 200
    with Image.open(io.BytesIO(index.seen[0])) as cropped:
        assert abs(cropped.width - 200) <= 1
        assert abs(cropped.height - 400) <= 1


def test_winescan_uses_own_search_when_native_fusion_is_enabled(app, client):
    """A native-engine deployment can switch providers without calling unsupported methods."""
    from dataclasses import replace

    app.state.settings = replace(app.state.settings, image_provider="winescan", cv_fusion=True)
    index = _with_recording_index(app)
    response = client.post(
        "/v1/scan/photo", files={"image": ("label.jpg", _photo_bytes(), "image/jpeg")},
        data={"box": "0.25,0.10,0.75,0.60"},
    )
    assert response.status_code == 200, response.text
    assert len(index.seen) == 1
    assert response.json()["slug"] == "shato-vymysel-cabernet"


def test_winescan_warmup_runs_search_and_reports_failure():
    from app.config import Settings
    from app.cv.factory import warm_up_image_index

    settings = Settings(image_provider="winescan")
    index = _RecordingIndex()
    assert warm_up_image_index(index, settings)
    assert len(index.seen) == 1

    class BrokenIndex:
        def search(self, image, top_k=5):
            raise RuntimeError("missing gallery")

    assert not warm_up_image_index(BrokenIndex(), settings)


def test_manual_box_uses_exif_oriented_coordinates():
    from PIL import ImageOps
    from app.cv.user_box import crop_to_box

    image = Image.new("RGB", (80, 40), "red")
    image.paste("blue", (40, 0, 80, 40))
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    result = Image.open(io.BytesIO(crop_to_box(buffer.getvalue(), (0, 0.5, 1, 1))))
    assert result.size == (40, 40)
    assert result.getpixel((20, 20))[2] > 200
    assert result.getexif().get(274) is None


def test_winescan_manual_box_skips_detector_through_http(app, client):
    import dataclasses
    from types import SimpleNamespace

    calls = []
    class Index(_RecordingIndex):
        def search_with_details(self, image, top_k, normalize):
            calls.append(normalize)
            return SimpleNamespace(matches=self.search(image, top_k), status="found", ocr_text="")
    index = Index()
    app.dependency_overrides[get_image_index_dep] = lambda: index
    app.state.settings = dataclasses.replace(app.state.settings, image_provider="winescan")
    for flat in (False, True):
        response = client.post(
            "/v1/scan/photo" + ("?flat=1" if flat else ""),
            files={"image": ("label.jpg", _photo_bytes(), "image/jpeg")},
            data={"box": "0.25,0.1,0.75,0.6"},
        )
        assert response.status_code == 200
    assert calls == [False, False]
