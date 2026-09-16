"""POST /v1/scan/photo — кейс ЛЦТ (contracts/image-scan.md v0.4).

Задачи волны: flat-формат байт-в-байт, "always best slug" при низкой
уверенности, rich-схема, timing_ms реально измеряется, near-dup ->
OCR-верификатор.
"""
from __future__ import annotations

import json
import time

from starlette.testclient import TestClient

from app.cv.interface import Match
from tests.conftest import auth_header, make_guest, register_user


def _photo(client: TestClient, payload: bytes, *, flat: bool = False, headers: dict | None = None):
    url = "/v1/scan/photo" + ("?flat=1" if flat else "")
    return client.post(url, files={"image": ("label.jpg", payload, "image/jpeg")}, headers=headers or {})


# --- flat mode: exact byte-for-byte contract, always best slug -------------

def test_flat_mode_is_exactly_one_key_slug_byte_for_byte(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=True)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # байт-в-байт: ровно {"slug": "..."} — ни одного лишнего поля.
    assert json.loads(r.text) == {"slug": "shato-vymysel-cabernet"}
    assert list(json.loads(r.text).keys()) == ["slug"]


def test_flat_mode_always_returns_best_slug_even_at_low_confidence(client: TestClient):
    """contracts/image-scan.md: "flat ВСЕГДА отдаёт лучший доступный slug,
    даже при низкой уверенности" — семантика скрипта оценки: пустой ответ
    гарантированно мимо, лучший кандидат имеет шанс."""
    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": "rozovyy-mirazh"}


def test_flat_mode_never_errors_on_empty_file(client: TestClient):
    r = _photo(client, b"", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_never_errors_on_oversized_file(client: TestClient, monkeypatch):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet" + b"0" * (9 * 1024 * 1024), flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_never_errors_on_unrecognized_photo(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:unknown", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_sequential_posts_all_valid_json(client: TestClient):
    """DoD image-scan.md: "последовательные POST -> каждый ответ — валидный
    плоский JSON" — минимальный прогон нескольких подряд."""
    payloads = [
        b"MOCKPHOTO:shato-vymysel-cabernet", b"MOCKPHOTO:unknown", b"",
        b"MOCKPHOTO:weak:igristoe-nebo-brut", b"MOCKPHOTO:near-dup",
    ]
    for payload in payloads:
        r = _photo(client, payload, flat=True)
        assert r.status_code == 200
        body = json.loads(r.text)
        assert set(body.keys()) == {"slug"}
        assert isinstance(body["slug"], str)


def test_flat_mode_works_without_any_auth_token(client: TestClient):
    """Скрипт оценки шлёт фото без Bearer-токена вообще (case.md не
    упоминает авторизацию) — эндпоинт обязан работать анонимно."""
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=True)
    assert r.status_code == 200


# --- rich mode: full schema --------------------------------------------------

def test_rich_mode_confident_match_full_schema(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "slug", "card", "confidence", "ocr_verified", "timing_ms",
        "not_in_catalog", "similar", "analogs", "matches",
    }
    assert body["slug"] == "shato-vymysel-cabernet"

    # v0.4.3 (пробел нашёл F): matches — top-5 {slug, score} по убыванию, для
    # eval-раннера (F1-top5 иначе вырождается в F1-top1 через живой API). UI
    # это поле не рендерит — тут только проверяем форму и что оно вообще есть.
    assert 1 <= len(body["matches"]) <= 5
    assert set(body["matches"][0].keys()) == {"slug", "score"}
    assert body["matches"][0]["slug"] == "shato-vymysel-cabernet"
    assert body["matches"][0]["score"] == body["confidence"]["top1_score"]
    scores = [m["score"] for m in body["matches"]]
    assert scores == sorted(scores, reverse=True), "matches обязаны идти по убыванию score"
    assert body["card"]["source"]["name"]
    assert body["card"]["derived"]["sensory"]
    assert body["not_in_catalog"] is False
    assert body["ocr_verified"] is False

    # v0.4.1 (пробел нашёл C): card — РОВНО тело GET /wines/{id}, включая
    # similar, не усечённая форма без него.
    assert set(body["card"].keys()) == {"wine_id", "source", "derived", "source_url", "similar"}
    assert body["card"]["wine_id"] == "shato-vymysel-cabernet"
    assert isinstance(body["card"]["similar"], list)

    wines_r = client.get(f"/v1/wines/{body['card']['wine_id']}", headers=auth_header(register_user(
        client, email="photo-card-parity@example.com")))
    assert wines_r.status_code == 200
    assert wines_r.json() == body["card"], "card в /scan/photo обязан буквально совпадать с GET /wines/{id}"

    conf = body["confidence"]
    assert set(conf.keys()) == {"top1_score", "gap", "f1_top1", "f1_top5", "eval_missing"}
    assert conf["top1_score"] > 0.9
    assert conf["eval_missing"] is True  # нет eval-отчёта — датасет кейса не приехал


def test_rich_mode_low_confidence_is_not_in_catalog_with_similar(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["card"] is None
    assert body["similar"]
    assert body["similar"][0]["wine_id"] == "rozovyy-mirazh"
    # v0.4.3: matches — про сырой ANN top-5 для eval, не про итоговое решение
    # "confident" — обязано быть заполнено И на низкой уверенности (иначе
    # F1-top5 именно на трудных случаях остался бы неизмерим через живой API).
    assert body["matches"]
    assert body["matches"][0]["slug"] == "rozovyy-mirazh"


def test_rich_mode_unrecognized_photo_is_honest_not_found(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:unknown", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["similar"] == []
    assert body["analogs"] == []
    assert body["matches"] == []  # v0.4.3: ANN ничего не нашёл — top-5 честно пуст


def test_rich_mode_empty_file_is_honest_400(client: TestClient):
    r = _photo(client, b"", flat=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_rich_mode_oversized_file_is_honest_400(client: TestClient):
    r = _photo(client, b"0" * (9 * 1024 * 1024), flat=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_rich_mode_works_for_guest_and_authenticated_user_too(client: TestClient):
    for headers in (auth_header(make_guest(client)), auth_header(register_user(client, email="photo1@example.com"))):
        r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False, headers=headers)
        assert r.status_code == 200


# --- near-dup -> OCR verifier -------------------------------------------------

def test_near_dup_top_candidates_trigger_ocr_verification(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:near-dup", flat=False)
    body = r.json()
    assert body["ocr_verified"] is True
    assert body["slug"] in {"mock-tainoe-vino-2022", "mock-tainoe-vino-2023"}


def test_near_dup_ocr_can_override_top_ann_choice(client: TestClient):
    """OCR "читает" 2022 явно — итоговый slug должен быть 2022, даже если
    top-1 ANN был 2023 (или наоборот) — ровно то, зачем верификатор нужен."""
    r = _photo(client, b"MOCKPHOTO:near-dup:mock-tainoe-vino-2022", flat=False)
    assert r.json()["slug"] == "mock-tainoe-vino-2022"
    assert r.json()["ocr_verified"] is True


def test_near_dup_ocr_failure_falls_back_to_top_ann_honestly(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:near-dup:NONE", flat=False)
    body = r.json()
    assert body["ocr_verified"] is False
    assert body["slug"] in {"mock-tainoe-vino-2022", "mock-tainoe-vino-2023"}


def test_flat_mode_near_dup_still_returns_ocr_resolved_slug(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:near-dup:mock-tainoe-vino-2022", flat=True)
    assert r.json() == {"slug": "mock-tainoe-vino-2022"}


# --- timing_ms genuinely measured, not hardcoded -----------------------------

class _SlowImageIndex:
    index_version = "slow-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        time.sleep(0.05)
        return [Match(slug="shato-vymysel-cabernet", score=0.95, gap=0.2, view="реальный")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_timing_ms_reflects_real_elapsed_time_not_hardcoded(client: TestClient, app):
    app.state.image_index = _SlowImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    # sleep(0.05) внутри search() -> минимум ~50 мс, оставляем запас на
    # таймер/шедулинг, но не даём пройти тесту на захардкоженный 0/константу.
    assert r.json()["timing_ms"] >= 40


def test_timing_ms_is_fast_on_default_mock(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert 0 <= r.json()["timing_ms"] < 1000
