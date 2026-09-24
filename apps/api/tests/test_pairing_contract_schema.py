"""Сверка ответов POST /v1/pairing/dish-photo и POST /v1/pairing/dish с
contracts/openapi.yaml 0.3.4 (`components.schemas.{DishPairingResponse,
DishInfo,PairingWineItem}`), поле в поле — задание тимлида 22.09 после
ратификации contracts/post-scan.md v1.1 §4/§5: "сверь ответы обоих эндпоинтов
поле в поле с openapi 0.3.4 и добавь тест, который валидирует ответ по
схеме".

Хэнд-роллед валидатор, не `jsonschema` (не в зависимостях проекта — заводить
новую ради одного теста дороже, чем написать точечную проверку; к тому же
`nullable: true` — диалект OpenAPI 3.0, не голый JSON Schema, обычный
валидатор его не понимает без надстройки). Тот же принцип точечной сверки со
схемой контракта, что уже есть в test_openapi_contract.py::
test_scan_resolve_response_fields_match_contract_or_are_documented — здесь
шире: не только набор полей, но и nullable/enum/maxItems по всему дереву
ответа."""
from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

from tests.conftest import auth_header, register_user
from tests.test_pairing_router import _dish_manual, _dish_photo, _mock_vlm, _patch_small_wine_catalog

OPENAPI_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"


def _load_schemas() -> dict:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    return spec["components"]["schemas"]


def _assert_value(value, prop_schema: dict, schemas: dict, *, path: str) -> None:
    if "$ref" in prop_schema:
        ref_name = prop_schema["$ref"].rsplit("/", 1)[-1]
        assert isinstance(value, dict), f"{path}: ожидался объект {ref_name}, получено {value!r}"
        _assert_object(value, ref_name, schemas, path=path)
        return
    if value is None:
        assert prop_schema.get("nullable"), f"{path}: null у поля без nullable:true в контракте"
        return
    prop_type = prop_schema.get("type")
    if prop_type == "string":
        assert isinstance(value, str), f"{path}: ожидалась строка, получено {value!r}"
        if "enum" in prop_schema:
            assert value in prop_schema["enum"], f"{path}: {value!r} вне enum {prop_schema['enum']}"
    elif prop_type == "integer":
        assert isinstance(value, int), f"{path}: ожидалось целое число, получено {value!r}"
    elif prop_type == "array":
        assert isinstance(value, list), f"{path}: ожидался массив, получено {value!r}"
        if "maxItems" in prop_schema:
            assert len(value) <= prop_schema["maxItems"], (
                f"{path}: длина {len(value)} превышает maxItems={prop_schema['maxItems']}"
            )
        items_schema = prop_schema["items"]
        for i, item in enumerate(value):
            _assert_value(item, items_schema, schemas, path=f"{path}[{i}]")
    else:
        raise AssertionError(f"{path}: не умею проверять тип схемы {prop_schema!r}")


def _assert_object(instance: dict, schema_name: str, schemas: dict, *, path: str) -> None:
    schema = schemas[schema_name]
    assert schema["type"] == "object", f"{schema_name}: контракт объявляет не object"
    props: dict = schema["properties"]
    required = set(schema.get("required", []))

    actual_keys = set(instance.keys())
    missing = required - actual_keys
    assert not missing, f"{path} ({schema_name}): нет обязательных полей контракта {missing}"
    extra = actual_keys - set(props.keys())
    assert not extra, f"{path} ({schema_name}): поля, которых нет в контракте {extra}"

    for key, value in instance.items():
        _assert_value(value, props[key], schemas, path=f"{path}.{key}")


def assert_dish_pairing_response_matches_contract(body: dict) -> None:
    schemas = _load_schemas()
    _assert_object(body, "DishPairingResponse", schemas, path="response")
    # dish.name — контракт: "НЕ null" (комментарий openapi.yaml::DishInfo.name)
    assert body["dish"]["name"] is not None
    assert isinstance(body["dish"]["name"], str)


# --------------------------------------------------------------------------
# dish-photo — все 4 статуса
# --------------------------------------------------------------------------

def test_dish_photo_food_response_matches_openapi_0_3_4(client: TestClient, app, monkeypatch):
    import dataclasses
    _patch_small_wine_catalog(monkeypatch, "Сыры")
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {
        "is_food": True, "is_wine_bottle": False, "dish": "Сырная тарелка",
        "ingredients": ["сыр", "мёд", "орехи", "виноград", "крекер", "лишний-шестой"],
        "category": "Сыры", "alternatives": ["BBQ", "Салаты", "лишний-третий"],
    })
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert_dish_pairing_response_matches_contract(body)
    assert body["status"] == "food"
    # openapi.yaml::DishInfo.ingredients "до 5", .alternatives "0-2" — модель
    # вернула больше, ответ обязан быть уже обрезан.
    assert len(body["dish"]["ingredients"]) <= 5
    assert len(body["dish"]["alternatives"]) <= 2


def test_dish_photo_not_food_response_matches_openapi_0_3_4(client: TestClient, app, monkeypatch):
    import dataclasses
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {"is_food": False, "is_wine_bottle": False})
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert_dish_pairing_response_matches_contract(body)
    assert body["status"] == "not_food"
    assert body["dish"]["category"] is None
    assert body["wines"] == []
    assert body["message"]


def test_dish_photo_bottle_response_matches_openapi_0_3_4(client: TestClient, app, monkeypatch):
    import dataclasses
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    _mock_vlm(monkeypatch, {"is_food": False, "is_wine_bottle": True})
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert_dish_pairing_response_matches_contract(body)
    assert body["status"] == "bottle"
    assert body["dish"]["category"] is None
    assert body["wines"] == []


def test_dish_photo_unsure_response_matches_openapi_0_3_4(client: TestClient):
    """Без VLM/локальной модели/zero-shot (MockImageIndex) — честный unsure,
    contract: alternatives = все 9 тегов, category=null."""
    r = _dish_photo(client, b"any-bytes")
    assert r.status_code == 200
    body = r.json()
    assert_dish_pairing_response_matches_contract(body)
    assert body["status"] == "unsure"
    assert body["dish"]["category"] is None
    assert len(body["dish"]["alternatives"]) == 9
    assert body["wines"] == []


# --------------------------------------------------------------------------
# dish (ручной) — всегда status=food по контракту
# --------------------------------------------------------------------------

def test_dish_manual_response_matches_openapi_0_3_4(client: TestClient, monkeypatch):
    _patch_small_wine_catalog(monkeypatch, "Сыры")
    tokens = register_user(client, email="pairing-schema-manual@example.com")
    r = _dish_manual(client, {"category": "Сыры", "dish": "Камамбер"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert_dish_pairing_response_matches_contract(body)
    assert body["status"] == "food"
    assert body["dish"]["source"] == "user"
    assert body["dish"]["alternatives"] == []
    assert body["dish"]["ingredients"] == []


def test_dish_manual_without_dish_name_response_matches_openapi_0_3_4(client: TestClient, monkeypatch):
    _patch_small_wine_catalog(monkeypatch, "BBQ")
    tokens = register_user(client, email="pairing-schema-manual-noname@example.com")
    r = _dish_manual(client, {"category": "BBQ"}, headers=auth_header(tokens))
    assert r.status_code == 200
    assert_dish_pairing_response_matches_contract(r.json())


# --------------------------------------------------------------------------
# Компонентные схемы существуют в контракте под именами, которые использует
# backend (PairingWineItem, не DishPairingWineItem — переименовано вслед за
# ратификацией, см. reports/backend-dish-photo.md).
# --------------------------------------------------------------------------

def test_contract_declares_pairing_wine_item_schema_by_this_exact_name():
    schemas = _load_schemas()
    assert "PairingWineItem" in schemas, (
        "app/schemas.py::PairingWineItem обязан называться так же, как схема в openapi.yaml"
    )
    assert schemas["DishPairingResponse"]["properties"]["wines"]["items"]["$ref"].endswith("PairingWineItem")
