"""POST /v1/eval/predict — эндпоинт скрипта кейсодержателя
(case-data/eval/participant_test.sh, agents/B3-eval-route.md).

Это алиас общего flat-обработчика (`app/routers/scan.py::flat_scan_response`)
— несгораемая семантика (contracts/image-scan.md v0.4/v0.4.4) уже подробно
покрыта `test_scan_photo.py` для `/scan/photo?flat=1`. Здесь — то, что
специфично для ЭТОГО фиксированного пути: нет query-развилки `?flat`, путь
сам по себе всегда flat, плюс явные требования брифа B3 — 200 на битом
файле, 200 на пустом поле, работа без auth, ровно `{"slug": str}`.
"""
from __future__ import annotations

import dataclasses
import json

from starlette.testclient import TestClient

from app.cv.interface import Match
from tests.conftest import auth_header, register_user


def _predict(client: TestClient, payload: bytes, *, field: str = "image", headers: dict | None = None):
    return client.post(
        "/v1/eval/predict",
        files={field: ("label.jpg", payload, "image/jpeg")},
        headers=headers or {},
    )


# --- ровно {"slug": str}, ничего лишнего, на mock-провайдере ----------------

def test_predict_returns_exactly_one_key_slug_on_mock_provider(client: TestClient):
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.text)
    assert body == {"slug": "shato-vymysel-cabernet"}
    assert list(body.keys()) == ["slug"]
    assert isinstance(body["slug"], str)


def test_predict_field_named_image_is_accepted(client: TestClient):
    """case-data/eval/participant_test.sh: curl --form 'image=@file' — поле
    буквально "image", основной задокументированный путь скрипта."""
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet", field="image")
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


def test_predict_accepts_first_file_field_regardless_of_name(client: TestClient):
    """Общий обработчик со /scan/photo — приём "первое файловое поле
    независимо от имени" (v0.4.4) сохраняется и здесь, доп. страховка сверх
    гарантированного скриптом имени "image"."""
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet", field="photo")
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


# --- жёсткое требование брифа: несгораемость на битом файле и пустом поле --

def test_predict_never_errors_on_corrupted_file(client: TestClient):
    """"Битый файл" — байты, не совпадающие ни с одной конвенцией mock-
    провайдера (не MOCKPHOTO-формат, не валидное фото вообще). Обязан
    остаться 200 + валидный ответ со slug'ом (пустым, если распознать
    нечего) — не 4xx/5xx."""
    r = _predict(client, b"\xff\xd8\xff\xe0not-a-real-jpeg\x00\x01garbage\xffbytes")
    assert r.status_code == 200
    body = json.loads(r.text)
    assert set(body.keys()) == {"slug"}
    assert isinstance(body["slug"], str)


def test_predict_never_errors_on_empty_field_value(client: TestClient):
    """Поле "image" присутствует, но пустое (0 байт)."""
    r = _predict(client, b"")
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_predict_never_errors_when_no_file_field_sent_at_all(client: TestClient):
    """Файлового поля нет вовсе (не просто пустое) — тоже честный 200 с
    лучшей (пустой) догадкой, не 400/422 фреймворка."""
    r = client.post("/v1/eval/predict", data={"not_a_file": "just text"})
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_predict_never_errors_on_oversized_file(client: TestClient):
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet" + b"0" * (26 * 1024 * 1024))
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


class _ExplodingImageIndex:
    """Сбой не в данных, а в самом пайплайне — та же проверка, что
    `test_scan_photo.py::test_flat_mode_survives_non_value_error_exception`,
    здесь через общий обработчик и другой путь."""

    index_version = "exploding-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        raise RuntimeError("не ValueError — ровно то, что flat обязан пережить")

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_predict_survives_arbitrary_pipeline_exception(client: TestClient, app):
    app.state.image_index = _ExplodingImageIndex()
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


# --- auth не требуется -------------------------------------------------------

def test_predict_works_without_any_auth_token(client: TestClient):
    """participant_test.sh шлёт запрос без Authorization вообще
    (case-data/eval/README.md не упоминает auth) — эндпоинт обязан отвечать
    анонимно."""
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200


def test_predict_does_not_require_bearer_token_even_if_one_is_sent(client: TestClient):
    """Токен не запрещён (просто не нужен) — если кто-то всё же продублирует
    вызов с токеном приложения, эндпоинт не должен на нём спотыкаться."""
    r = _predict(
        client, b"MOCKPHOTO:shato-vymysel-cabernet",
        headers=auth_header(register_user(client, email="eval-predict-auth@example.com")),
    )
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


# --- always best slug, даже низкая уверенность / near-dup -------------------

def test_predict_always_returns_best_slug_even_at_low_confidence(client: TestClient):
    r = _predict(client, b"MOCKPHOTO:weak:rozovyy-mirazh")
    assert r.status_code == 200
    assert r.json() == {"slug": "rozovyy-mirazh"}


def test_predict_near_dup_returns_ocr_resolved_slug(client: TestClient):
    r = _predict(client, b"MOCKPHOTO:near-dup:mock-tainoe-vino-2022")
    assert r.status_code == 200
    assert r.json() == {"slug": "mock-tainoe-vino-2022"}


# --- фиксированный путь: не зависит от ?flat / SCAN_FLAT_DEFAULT ------------

def test_predict_ignores_flat_query_param(client: TestClient):
    """/v1/eval/predict — не /scan/photo: query-развилки ?flat=0/1 тут нет
    вообще, путь сам по себе всегда flat. Незнакомый query-параметр FastAPI
    молча игнорирует — не должен переключать эндпоинт ни во что другое."""
    r = client.post(
        "/v1/eval/predict?flat=0",
        files={"image": ("label.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


def test_predict_unaffected_by_scan_flat_default_env(client: TestClient, app):
    """SCAN_FLAT_DEFAULT переключает /scan/photo без query-параметра (см.
    test_scan_photo.py) — /v1/eval/predict эту настройку не читает вовсе,
    путь всегда flat независимо от её значения."""
    app.state.settings = dataclasses.replace(app.state.settings, scan_flat_default=True)
    r = _predict(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


# --- последовательные POST — ровно то, как их шлёт participant_test.sh -----

def test_predict_sequential_posts_all_valid_json(client: TestClient):
    payloads = [
        b"MOCKPHOTO:shato-vymysel-cabernet", b"MOCKPHOTO:unknown", b"",
        b"MOCKPHOTO:weak:igristoe-nebo-brut", b"MOCKPHOTO:near-dup",
    ]
    for payload in payloads:
        r = _predict(client, payload)
        assert r.status_code == 200
        body = json.loads(r.text)
        assert set(body.keys()) == {"slug"}
        assert isinstance(body["slug"], str)


# --- первый шаг: кадр больше 1024 px уменьшается до большей стороны 1024 ----

def _jpeg(size: tuple[int, int], orientation: int | None = None) -> bytes:
    import io

    from PIL import Image

    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    buffer = io.BytesIO()
    Image.new("RGB", size, (120, 30, 60)).save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def _size(data: bytes) -> tuple[int, int]:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return image.size


def test_downscale_landscape_keeps_proportions():
    from app.cv.downscale import downscale_to_max_side

    assert _size(downscale_to_max_side(_jpeg((3000, 2000)), 1024)) == (1024, 683)


def test_downscale_portrait_keeps_proportions():
    from app.cv.downscale import downscale_to_max_side

    assert _size(downscale_to_max_side(_jpeg((3024, 4032)), 1024)) == (768, 1024)


def test_downscale_when_only_one_side_exceeds_limit():
    from app.cv.downscale import downscale_to_max_side

    assert _size(downscale_to_max_side(_jpeg((1500, 600)), 1024)) == (1024, 410)


def test_downscale_applies_exif_orientation():
    """Телефонный снимок: пиксели лежат 4032×3024, EXIF велит повернуть на 90°."""
    from app.cv.downscale import downscale_to_max_side

    assert _size(downscale_to_max_side(_jpeg((4032, 3024), orientation=6), 1024)) == (768, 1024)


def test_downscale_leaves_small_image_bytes_untouched():
    from app.cv.downscale import downscale_to_max_side

    for size in [(1024, 1024), (1024, 700), (800, 600)]:
        data = _jpeg(size)
        assert downscale_to_max_side(data, 1024) is data


def test_downscale_passes_through_undecodable_bytes():
    from app.cv.downscale import downscale_to_max_side

    for data in [b"MOCKPHOTO:shato-vymysel-cabernet", b"\xff\xd8\xff\xe0garbage"]:
        assert downscale_to_max_side(data, 1024) is data


class _RecordingImageIndex:
    index_version = "recording-stub"

    def __init__(self) -> None:
        self.seen: list[bytes] = []

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        self.seen.append(image)
        return []

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_predict_hands_downscaled_image_to_pipeline(client: TestClient, app):
    index = _RecordingImageIndex()
    app.state.image_index = index
    r = _predict(client, _jpeg((2048, 1536)))
    assert r.status_code == 200
    assert index.seen and _size(index.seen[0]) == (1024, 768)
