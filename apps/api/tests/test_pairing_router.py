"""POST /v1/pairing/dish-photo, POST /v1/pairing/dish — "Что подать" по фото
блюда (contracts/post-scan.md v1.1 по брифу тимлида 22.09). Паттерн — тот же,
что tests/test_scan_photo.py (multipart) и tests/test_wine_pairings.py
(register_user/auth_header). Сеть не трогается: `vision_llm.ask_json_or_raise`
подменяется, как в test_vision_llm.py/test_dish_recognition.py."""
from __future__ import annotations

import dataclasses

from starlette.testclient import TestClient

from app.cv import vision_llm
from tests.conftest import auth_header, make_guest, register_user


def _dish_photo(client: TestClient, payload: bytes, *, headers: dict | None = None):
    return client.post(
        "/v1/pairing/dish-photo",
        files={"image": ("dish.jpg", payload, "image/jpeg")},
        headers=headers or {},
    )


def _dish_manual(client: TestClient, body: dict, *, headers: dict | None = None):
    return client.post("/v1/pairing/dish", json=body, headers=headers or {})


# --------------------------------------------------------------------------
# dish-photo — лимиты/авторизация как у /v1/scan/photo
# --------------------------------------------------------------------------

def test_dish_photo_works_without_any_auth_token(client: TestClient):
    r = _dish_photo(client, b"any-bytes-mock-cv-degrades-gracefully")
    assert r.status_code == 200


def test_dish_photo_attributes_scan_to_authorized_principal_but_does_not_require_it(client: TestClient):
    tokens = register_user(client, email="dish-photo-auth@example.com")
    r = _dish_photo(client, b"any-bytes", headers=auth_header(tokens))
    assert r.status_code == 200


def test_dish_photo_empty_file_is_400_validation_error(client: TestClient):
    r = client.post("/v1/pairing/dish-photo", files={"image": ("dish.jpg", b"", "image/jpeg")})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_dish_photo_missing_file_is_400_validation_error(client: TestClient):
    r = client.post("/v1/pairing/dish-photo")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_dish_photo_oversized_file_is_400_validation_error(client: TestClient):
    payload = b"x" * (26 * 1024 * 1024)  # тот же лимит 25 МБ, что /v1/scan/photo
    r = _dish_photo(client, payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_dish_photo_does_not_write_to_scan_archive(client: TestClient, app, tmp_path):
    """Бриф тимлида: "В архив сканов НЕ писать" — даже когда SCAN_ARCHIVE_DIR
    настроен (как на демо-стенде), эта ручка не должна класть туда ни файла."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    app.state.settings = dataclasses.replace(app.state.settings, scan_archive_dir=str(archive_dir))

    r = _dish_photo(client, b"MOCKPHOTO:shato-vymysel-cabernet")
    assert r.status_code == 200
    assert list(archive_dir.iterdir()) == [], "dish-photo не обязан архивировать фото блюда"


def test_dish_photo_has_timing_ms(client: TestClient):
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    assert isinstance(r.json()["timing_ms"], int) and r.json()["timing_ms"] >= 0


# --------------------------------------------------------------------------
# dish-photo — статусы через мок VLM-шлюза (JSON-ответ модели)
# --------------------------------------------------------------------------

def _mock_vlm(monkeypatch, payload: dict):
    monkeypatch.setattr(vision_llm, "ask_json_or_raise", lambda image_bytes, **kw: payload)


def test_dish_photo_status_food_with_recognized_category(client: TestClient, app, monkeypatch):
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {
        "is_food": True, "is_wine_bottle": False, "dish": "Сырная тарелка",
        "ingredients": ["сыр", "мёд"], "category": "Сыры", "alternatives": [],
    })
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "food"
    assert body["dish"] == {
        "name": "Сырная тарелка", "category": "Сыры", "alternatives": [],
        "ingredients": ["сыр", "мёд"], "source": "vlm",
    }
    assert body["message"] is None
    assert 1 <= len(body["wines"]) <= 6
    assert all(w["basis"] in ("catalog", "rules") for w in body["wines"])


def test_dish_photo_status_not_food(client: TestClient, app, monkeypatch):
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {"is_food": False, "is_wine_bottle": False})
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_food"
    assert body["wines"] == []
    assert body["message"]


def test_dish_photo_status_bottle(client: TestClient, app, monkeypatch):
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {"is_food": False, "is_wine_bottle": True})
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "bottle"
    assert body["wines"] == []
    assert body["message"]


def test_dish_photo_status_unsure_when_category_unresolvable(client: TestClient, app, monkeypatch):
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {"is_food": True, "is_wine_bottle": False, "category": "неизвестная науке кухня"})
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unsure"
    assert body["wines"] == []
    assert len(body["dish"]["alternatives"]) == 9
    assert body["message"]


def test_dish_photo_status_unsure_without_any_model_or_zero_shot(client: TestClient):
    """MockImageIndex не несёт `.encoder` — zero-shot недоступен, ни шлюз, ни
    локальная VLM не настроены в тестовом окружении по умолчанию -> честный
    unsure, а не ошибка/зависание."""
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unsure"
    assert body["dish"]["source"] == "none"
    assert len(body["dish"]["alternatives"]) == 9


def test_dish_photo_gateway_failure_falls_back_to_unsure_not_500(client: TestClient, app, monkeypatch):
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")

    def failing(image_bytes, **kw):
        raise vision_llm.VisionLLMError("шлюз лёг")

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", failing)
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    assert r.json()["status"] == "unsure"


# --------------------------------------------------------------------------
# dish — ручной выбор/исправление категории
# --------------------------------------------------------------------------

def test_dish_manual_requires_auth(client: TestClient):
    r = _dish_manual(client, {"category": "Сыры"})
    assert r.status_code == 401


def test_dish_manual_guest_is_allowed(client: TestClient):
    tokens = make_guest(client)
    r = _dish_manual(client, {"category": "Сыры"}, headers=auth_header(tokens))
    assert r.status_code == 200


def test_dish_manual_valid_category_returns_food_with_user_source(client: TestClient):
    tokens = register_user(client, email="dish-manual@example.com")
    r = _dish_manual(client, {"category": "сыры", "dish": "Камамбер"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "food"
    assert body["dish"] == {
        "name": "Камамбер", "category": "Сыры", "alternatives": [], "ingredients": [], "source": "user",
    }
    assert len(body["wines"]) >= 1


def test_dish_manual_category_typo_is_fuzzy_corrected(client: TestClient):
    tokens = register_user(client, email="dish-manual-fuzzy@example.com")
    r = _dish_manual(client, {"category": "Азиятская кухня"}, headers=auth_header(tokens))
    assert r.status_code == 200
    assert r.json()["dish"]["category"] == "Азиатская кухня"


def test_dish_manual_unknown_category_is_400_validation_error(client: TestClient):
    tokens = register_user(client, email="dish-manual-invalid@example.com")
    r = _dish_manual(client, {"category": "квантовая физика"}, headers=auth_header(tokens))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_dish_manual_dish_name_optional(client: TestClient):
    tokens = register_user(client, email="dish-manual-no-name@example.com")
    r = _dish_manual(client, {"category": "Сыры"}, headers=auth_header(tokens))
    assert r.status_code == 200
    assert r.json()["dish"]["name"] == ""


def test_dish_manual_deterministic_across_repeated_calls(client: TestClient):
    tokens = register_user(client, email="dish-manual-determinism@example.com")
    headers = auth_header(tokens)
    r1 = _dish_manual(client, {"category": "Сыры"}, headers=headers)
    r2 = _dish_manual(client, {"category": "Сыры"}, headers=headers)
    b1, b2 = r1.json(), r2.json()
    b1.pop("timing_ms")
    b2.pop("timing_ms")
    assert b1 == b2
