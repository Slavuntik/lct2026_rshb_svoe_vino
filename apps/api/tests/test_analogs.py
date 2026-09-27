from __future__ import annotations

from starlette.testclient import TestClient

from app.rag.fixtures import REFERENCE_STYLES
from tests.conftest import auth_header, register_user


def test_analogs_resolves_style_and_returns_wines(client: TestClient):
    tokens = register_user(client, email="an1@example.com")
    r = client.post("/v1/analogs", json={"query": "люблю Просекко"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["style"]["slug"] == "prosecco"
    assert len(body["wines"]) >= 1
    assert body["wines"][0]["wine_id"] == "igristoe-nebo-brut"
    assert len(body["wines"]) <= 12


def test_analogs_unrecognized_style_is_404_with_honest_message(client: TestClient):
    """reports/qa-manual-final.md #4 / reports/backend-analogs-empty.md: пустой
    результат резолва стиля — честный текст без слов "ошибка"/"не удалось" и
    БЕЗ перечисления внутренних (иностранных) эталонных стилей каталога —
    раньше здесь были живые имена REFERENCE_STYLES фикстур ("Просекко" и т.п.)."""
    tokens = register_user(client, email="an2@example.com")
    r = client.post("/v1/analogs", json={"query": "совершенно неразборчивый набор слов xyz"},
                     headers=auth_header(tokens))
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
    message = body["error"]["message"]
    assert "не наш" in message.lower()
    assert "не удалось" not in message.lower()
    for style in REFERENCE_STYLES:
        assert style["name"] not in message


def test_analogs_autochthonous_grape_has_no_internal_style_leak(client: TestClient):
    """Прямой повтор находки qa-manual (27.09, reports/qa-manual-final.md #4,
    P2 но видна жюри): карточка автохтонного российского сорта не резолвится
    в иностранный стиль — 404 с честным сообщением, без единого имени
    внутреннего (иностранного) эталонного стиля каталога в тексте."""
    tokens = register_user(client, email="an-autochthonous@example.com")
    for grape in ("Красностоп Золотовский", "Цимлянский чёрный", "Рубин Голодриги"):
        r = client.post("/v1/analogs", json={"query": grape}, headers=auth_header(tokens))
        assert r.status_code == 404, grape
        body = r.json()
        assert body["error"]["code"] == "not_found"
        message = body["error"]["message"]
        assert "Популярные стили" not in message
        for style in REFERENCE_STYLES:
            assert style["name"] not in message


def test_analogs_filters_by_region(client: TestClient):
    tokens = register_user(client, email="an3@example.com")
    r = client.post("/v1/analogs", json={
        "query": "что-то как Вальполичелла", "filters": {"region": "kuban"},
    }, headers=auth_header(tokens))
    assert r.status_code == 200
    for wine in r.json()["wines"]:
        assert wine["region_name"] == "Кубань"


def test_analogs_requires_auth(client: TestClient):
    r = client.post("/v1/analogs", json={"query": "просекко"})
    assert r.status_code == 401
