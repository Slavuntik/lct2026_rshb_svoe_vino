"""GET /v1/case-thumbs/{slug}.webp (contracts/image-scan.md v0.4.11 п.4,
агент B8; app/routers/case_thumbs.py). Файлы сами генерирует офлайн
apps/api/scripts/build_case_thumbs.py — здесь тестируется ТОЛЬКО раздача
(валидация слага, 404, кэш-заголовки), не сам ресайз.
"""
from __future__ import annotations

from starlette.testclient import TestClient


def _write_thumb(tmp_path, slug: str, content: bytes = b"fake-webp-bytes") -> None:
    thumbs_dir = tmp_path / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    (thumbs_dir / f"{slug}.webp").write_bytes(content)


def test_thumb_is_served_when_file_exists(client: TestClient, tmp_path, monkeypatch):
    _write_thumb(tmp_path, "shato-vymysel-cabernet", b"\x00\x01binary-webp-content")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/shato-vymysel-cabernet.webp")

    assert r.status_code == 200
    assert r.content == b"\x00\x01binary-webp-content"
    assert r.headers["content-type"] == "image/webp"


def test_thumb_has_cache_headers(client: TestClient, tmp_path, monkeypatch):
    _write_thumb(tmp_path, "some-slug")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/some-slug.webp")

    assert r.status_code == 200
    assert "max-age" in r.headers.get("cache-control", "")


def test_thumb_requires_no_auth(client: TestClient, tmp_path, monkeypatch):
    """Та же дисциплина, что и /scan/photo, /metrics/scan — карточка-фолбэк
    рендерит <img src> без токена."""
    _write_thumb(tmp_path, "no-auth-slug")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/no-auth-slug.webp")  # без Authorization

    assert r.status_code == 200


def test_thumb_404_when_slug_valid_but_file_missing(client: TestClient, tmp_path, monkeypatch):
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))  # thumbs/ вовсе нет

    r = client.get("/v1/case-thumbs/does-not-exist.webp")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_thumb_404_without_case_data_dir_at_all(client: TestClient, tmp_path, monkeypatch):
    """CASE_DATA_DIR указывает на директорию без thumbs/ вовсе — 404, не 500
    (честная деградация, тот же принцип, что и у case_catalog.py)."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/anything.webp")

    assert r.status_code == 404


def test_thumb_404_on_uppercase_slug(client: TestClient, tmp_path, monkeypatch):
    """Регекс слага — lower-case only (реальные слаги кейса все lower-case,
    см. case-data/slug_refs.json) — строгий allowlist, не просто "нет '..'"."""
    _write_thumb(tmp_path, "abc")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/ABC.webp")

    assert r.status_code == 404


def test_thumb_404_on_dot_dot_shaped_slug(client: TestClient, tmp_path, monkeypatch):
    """Попытка обхода пути (DoD contracts/image-scan.md v0.4.11 п.6): "." не
    входит в allowlist слага вовсе — "foo..bar" (traversal-подобная форма,
    без разделителей "/") обязан 404, не 200/500."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    r = client.get("/v1/case-thumbs/foo..bar.webp")

    assert r.status_code == 404


def test_thumb_path_traversal_via_encoded_slashes_does_not_leak_files(
    client: TestClient, tmp_path, monkeypatch
):
    """Второй рубеж (принадлежность resolved-пути thumbs_dir) — на случай,
    если какой-то ASGI-сервер декодирует "%2e%2e%2f" до маршрутизации.
    Каким бы путём запрос ни попал в обработчик, наружу не должен уйти файл
    ВНЕ thumbs_dir ни с каким кодом, кроме 404 (никакого 200 с чужим
    содержимым)."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    outside = tmp_path / "secret.txt"
    outside.write_text("не должно утечь", encoding="utf-8")

    r = client.get("/v1/case-thumbs/%2e%2e%2fsecret.txt.webp")

    assert r.status_code in (404, 400), r.text
    assert "не должно утечь" not in r.text


def test_thumb_route_registered_under_v1_prefix(client: TestClient, tmp_path, monkeypatch):
    """Форма пути буквально как в контракте: GET /v1/case-thumbs/{slug}.webp."""
    _write_thumb(tmp_path, "path-shape-check")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    app_schema = client.app.openapi()
    assert "/v1/case-thumbs/{slug}.webp" in app_schema["paths"]
    assert "get" in app_schema["paths"]["/v1/case-thumbs/{slug}.webp"]
