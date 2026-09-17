"""DoD (agents/B-api.md): "openapi-схема FastAPI совпадает с контрактом по
путям/методам (тест-сверка)". Источники истины — contracts/openapi.yaml
(основной контракт) И contracts/image-scan.md (кейс ЛЦТ, волна сканера по
фото — /scan/photo и /metrics/scan описаны ТОЛЬКО там, оркестратор осознанно
не сливал их в openapi.yaml). Читаем оба файла напрямую (а не переписываем
пути руками), чтобы тест не мог разойтись с контрактом молча.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from starlette.testclient import TestClient

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
OPENAPI_PATH = CONTRACTS_DIR / "openapi.yaml"
IMAGE_SCAN_PATH = CONTRACTS_DIR / "image-scan.md"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}

# contracts/image-scan.md пишет пути в прозе как `METHOD /v1/path` в
# backticks (не OpenAPI YAML) — например "`POST /v1/scan/photo`",
# "`GET /v1/metrics/scan`".
_MD_PATH_RE = re.compile(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/v1/[a-zA-Z0-9/_-]+)`")


def _load_openapi_contract_paths() -> dict[str, set[str]]:
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    server_prefix = spec["servers"][0]["url"].rstrip("/")  # "/v1"
    result: dict[str, set[str]] = {}
    for path, methods in spec["paths"].items():
        full_path = server_prefix + path
        result[full_path] = {m.upper() for m in methods if m in _HTTP_METHODS}
    return result


def _load_image_scan_contract_paths() -> dict[str, set[str]]:
    text = IMAGE_SCAN_PATH.read_text(encoding="utf-8")
    result: dict[str, set[str]] = {}
    for method, path in _MD_PATH_RE.findall(text):
        result.setdefault(path, set()).add(method.upper())
    return result


def _load_all_contract_paths() -> dict[str, set[str]]:
    merged = _load_openapi_contract_paths()
    for path, methods in _load_image_scan_contract_paths().items():
        merged.setdefault(path, set()).update(methods)
    return merged


def test_contract_paths_and_methods_are_all_implemented(client: TestClient):
    assert OPENAPI_PATH.exists(), f"контракт не найден: {OPENAPI_PATH}"
    assert IMAGE_SCAN_PATH.exists(), f"контракт не найден: {IMAGE_SCAN_PATH}"
    contract = _load_all_contract_paths()
    assert "/v1/scan/photo" in contract and "/v1/metrics/scan" in contract, (
        "парсер contracts/image-scan.md не нашёл ожидаемые пути — regex разошёлся с форматом файла"
    )

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
    множества путей совпадают 1:1 по обоим контрактам — фиксируем это как
    регресс-тест: если кто-то добавит эндпоинт мимо обоих контрактов, тест
    это заметит.

    v0.4.6 (коммит 1f3e6e9): временное именованное исключение для
    `/v1/eval/predict` (было в этом месте — см. git-историю файла,
    reports/b3-eval-route.md "Предложения к контрактам") снято — оркестратор
    вписал путь в contracts/image-scan.md, тест снова проверяет точное
    совпадение без изъятий."""
    contract = _load_all_contract_paths()
    app_schema = client.app.openapi()
    app_paths = set(app_schema["paths"])
    extra = sorted(app_paths - set(contract))
    assert extra == [], f"в приложении есть пути, которых нет в контракте: {extra}"


# agents/B7-foreign-analogs.md: POST /v1/scan/resolve получил два новых
# ОПЦИОНАЛЬНЫХ поля ответа — analogs (та же форма, что у /v1/analogs) и
# analog_reason — фолбэк на пустых matches: узнанный по pipeline/ref
# сорт/стиль -> аналоги российских вин. Путь /scan/resolve в контракте уже
# есть (проверка выше её не ловит — та смотрит только на пути/методы, не на
# поля тела ответа), но САМИ поля пока не описаны в contracts/openapi.yaml —
# правка contracts/ вне зоны B7 (ORCHESTRATION.md: "контракты меняет только
# оркестратор"; B7-foreign-analogs.md: "схему ответа в openapi.yaml обновит
# оркестратор по твоему отчёту"). Тот же паттерн именованного исключения, что
# B3 применял к путям (_KNOWN_UNDOCUMENTED_EXTRA_PATHS выше, по всей истории
# файла) — здесь на уровне полей одного ответа. Когда оркестратор впишет оба
# поля в contracts/openapi.yaml, запись можно убрать вместе с проверкой, см.
# reports/b7-foreign-analogs.md.
_KNOWN_UNDOCUMENTED_RESPONSE_FIELDS: dict[str, frozenset[str]] = {
    "/v1/scan/resolve": frozenset({"analogs", "analog_reason"}),
}


def _resolve_schema_ref(app_schema: dict, schema: dict) -> dict:
    ref = schema.get("$ref")
    if ref is None:
        return schema
    node = app_schema
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def test_scan_resolve_response_fields_match_contract_or_are_documented(client: TestClient):
    """Более тонкая, чем "путь есть" — сверяет СВОЙСТВА тела ответа 200 у
    /scan/resolve с contracts/openapi.yaml, с единственным исключением
    _KNOWN_UNDOCUMENTED_RESPONSE_FIELDS выше (не общая лазейка: и пропажа
    контрактного поля, и ЛЮБОЕ ДРУГОЕ недокументированное поле по-прежнему
    ловятся)."""
    contract_schema = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    contract_props = set(
        contract_schema["paths"]["/scan/resolve"]["post"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["properties"].keys()
    )

    app_schema = client.app.openapi()
    raw = app_schema["paths"]["/v1/scan/resolve"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    resolved = _resolve_schema_ref(app_schema, raw)
    app_props = set(resolved.get("properties", {}).keys())

    unexpected_extra = app_props - contract_props
    assert not unexpected_extra, f"недокументированные новые поля ответа /scan/resolve: {unexpected_extra}"
    missing = contract_props - app_props
    assert not missing, f"поля контракта пропали из ответа /scan/resolve: {missing}"
