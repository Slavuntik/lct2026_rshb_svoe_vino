"""DoD (agents/B-api.md): "openapi-схема FastAPI совпадает с контрактом по
путям/методам (тест-сверка)". Источник истины — contracts/openapi.yaml,
читаем его напрямую (а не переписываем пути руками), чтобы тест не мог
разойтись с контрактом молча.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from starlette.testclient import TestClient

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _load_contract_paths() -> dict[str, set[str]]:
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    server_prefix = spec["servers"][0]["url"].rstrip("/")  # "/v1"
    result: dict[str, set[str]] = {}
    for path, methods in spec["paths"].items():
        full_path = server_prefix + path
        result[full_path] = {m.upper() for m in methods if m in _HTTP_METHODS}
    return result


def test_contract_paths_and_methods_are_all_implemented(client: TestClient):
    assert CONTRACT_PATH.exists(), f"контракт не найден: {CONTRACT_PATH}"
    contract = _load_contract_paths()
    app_schema = client.app.openapi()
    app_paths: dict[str, set[str]] = {
        path: {m.upper() for m in methods if m in _HTTP_METHODS}
        for path, methods in app_schema["paths"].items()
    }

    missing_paths = sorted(set(contract) - set(app_paths))
    assert not missing_paths, f"путей из контракта нет в приложении: {missing_paths}"

    method_mismatches = []
    for path, methods in contract.items():
        missing_methods = methods - app_paths.get(path, set())
        if missing_methods:
            method_mismatches.append((path, sorted(missing_methods)))
    assert not method_mismatches, f"методов из контракта нет в приложении: {method_mismatches}"


def test_contract_paths_match_app_exactly_no_undocumented_extras(client: TestClient):
    """Не требование DoD буквально (там речь только о "контракт покрыт"), но
    на v0.2 множества путей совпадают 1:1 — фиксируем это как регресс-тест:
    если кто-то добавит эндпоинт мимо контракта, тест это заметит."""
    contract = _load_contract_paths()
    app_schema = client.app.openapi()
    app_paths = set(app_schema["paths"])
    extra = sorted(app_paths - set(contract))
    assert extra == [], f"в приложении есть пути, которых нет в контракте: {extra}"
