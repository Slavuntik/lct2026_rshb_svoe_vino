"""GET /v1/catalog — плитка каталога кейса (задача тимлида 27.09, экран
«Каталог вин» по макету Figma). Источник данных подменяется на уровне
`dish_pairing._iter_catalog_cards` (тот же приём, что tests/test_dish_pairing.py
использует для подбора к блюду — оба роутера читают один кэш) — ни
`case_catalog.json`, ни retriever, ни файлы превью не трогаются.
`case_catalog.has_thumb()` подменяется точечно, где тест именно про
`image_url`."""
from __future__ import annotations

from starlette.testclient import TestClient

from app import dish_pairing
from app.routers import catalog as catalog_router
from tests.conftest import auth_header, make_guest, register_user


def _card(
    wine_id: str, *, name: str | None = None, winery: str | None = "Тестовая винодельня",
    color: str | None = "красное", sugar: str | None = "сухое",
) -> tuple[str, dict, None]:
    source = {
        "name": name if name is not None else f"Вино {wine_id}",
        "winery_name": winery,
        "color": color,
        "sugar_category": sugar,
    }
    return (wine_id, source, None)


def _patch_catalog(monkeypatch, cards: list[tuple[str, dict, None]]) -> None:
    monkeypatch.setattr(dish_pairing, "_iter_catalog_cards", lambda retriever, settings: tuple(cards))


def _patch_has_thumb(monkeypatch, slugs_with_thumb: set[str]) -> None:
    monkeypatch.setattr(catalog_router.case_catalog, "has_thumb", lambda slug: slug in slugs_with_thumb)


def _auth(client: TestClient) -> dict:
    return auth_header(register_user(client, email=f"catalog-{id(client)}@example.com"))


# --- авторизация — как у соседних /wines/{id} и /analogs -------------------

def test_catalog_requires_auth(client: TestClient):
    r = client.get("/v1/catalog")
    assert r.status_code == 401


def test_catalog_allows_guest(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1")])
    r = client.get("/v1/catalog", headers=auth_header(make_guest(client)))
    assert r.status_code == 200


# --- форма ответа / дефолты -------------------------------------------------

def test_catalog_default_shape_and_fields(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1", name="Совиньон", winery="Шато", color="белое", sugar="сухое")])
    _patch_has_thumb(monkeypatch, {"w1"})

    r = client.get("/v1/catalog", headers=_auth(client))

    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["limit"] == 24
    assert body["offset"] == 0
    assert body["wines"] == [{
        "wine_id": "w1", "name": "Совиньон", "winery": "Шато",
        "color": "белое", "sugar": "сухое",
        "image_url": "/v1/case-thumbs/w1.webp",
    }]


def test_image_url_is_null_when_no_thumb_file_wine_not_dropped(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("no-thumb-wine")])
    _patch_has_thumb(monkeypatch, set())  # ни у кого нет файла

    r = client.get("/v1/catalog", headers=_auth(client))

    assert r.status_code == 200
    wines = r.json()["wines"]
    assert len(wines) == 1  # не выброшено из выдачи
    assert wines[0]["image_url"] is None


def test_wine_missing_winery_or_sugar_data_serializes_as_null_not_500(client: TestClient, monkeypatch):
    """~125 слагов вне настоящего RAG-индекса (packages/rag/rag/case_data.py)
    не несут sugar_category вовсе — честный null, не 500."""
    _patch_catalog(monkeypatch, [_card("bare", winery=None, sugar=None)])
    r = client.get("/v1/catalog", headers=_auth(client))
    assert r.status_code == 200
    wine = r.json()["wines"][0]
    assert wine["winery"] is None
    assert wine["sugar"] is None


# --- постраничность: без дублей и пропусков ---------------------------------

def test_pagination_no_duplicates_no_gaps(client: TestClient, monkeypatch):
    cards = [_card(f"wine-{i:03d}", name=f"Вино {i:03d}") for i in range(50)]
    _patch_catalog(monkeypatch, cards)
    headers = _auth(client)

    seen: list[str] = []
    offset = 0
    limit = 7
    for _ in range(20):  # 50/7 -> 8 страниц, запас с лихвой против зацикливания
        r = client.get(f"/v1/catalog?limit={limit}&offset={offset}", headers=headers)
        assert r.status_code == 200
        page = r.json()["wines"]
        if not page:
            break
        seen.extend(w["wine_id"] for w in page)
        offset += limit

    assert len(seen) == 50
    assert len(set(seen)) == 50, "постраничная выдача не должна дублировать позиции"
    assert seen == sorted(seen), "порядок между страницами обязан быть тем же, что и внутри одной"


def test_default_limit_is_24(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card(f"wine-{i:03d}") for i in range(30)])
    r = client.get("/v1/catalog", headers=_auth(client))
    body = r.json()
    assert len(body["wines"]) == 24
    assert body["total"] == 30


def test_offset_beyond_total_returns_empty_page_not_error(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("only-one")])
    r = client.get("/v1/catalog?offset=100", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["wines"] == []
    assert body["total"] == 1  # total — это ПОСЛЕ фильтров, ДО среза offset/limit


def test_empty_catalog_returns_empty_list_not_error(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [])
    r = client.get("/v1/catalog", headers=_auth(client))
    assert r.status_code == 200
    assert r.json() == {"wines": [], "total": 0, "limit": 24, "offset": 0}


# --- поиск q: по названию и винодельне, регистронезависимо ------------------

def test_search_matches_name_case_insensitive(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1", name="Каберне Совиньон"), _card("w2", name="Совсем другое")])
    r = client.get("/v1/catalog?q=СовиньОн", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["w1"]


def test_search_matches_winery(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [
        _card("w1", name="Вино А", winery="Шато Тамань"),
        _card("w2", name="Вино Б", winery="Абрау-Дюрсо"),
    ])
    r = client.get("/v1/catalog?q=тамань", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["w1"]


def test_search_no_match_returns_empty_not_error(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1", name="Вино")])
    r = client.get("/v1/catalog?q=нигдетакогонет", headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["wines"] == []
    assert r.json()["total"] == 0


def test_blank_search_is_treated_as_no_filter(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1")])
    r = client.get("/v1/catalog?q=%20%20", headers=_auth(client))  # "  " — только пробелы
    assert r.json()["total"] == 1


# --- фильтры color/sugar -----------------------------------------------------

def test_filter_by_color(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("red1", color="красное"), _card("white1", color="белое")])
    r = client.get("/v1/catalog?color=белое", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["white1"]


def test_filter_by_sugar(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("dry1", sugar="сухое"), _card("sweet1", sugar="сладкое")])
    r = client.get("/v1/catalog?sugar=сладкое", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["sweet1"]


def test_color_filter_is_case_insensitive_against_stored_value(client: TestClient, monkeypatch):
    """Регресс на реальную находку (reports/backend-catalog-list.md): у
    слагов, резолвящихся ТОЛЬКО через app/rag/case_catalog.py (не через
    настоящий RAG и не через супплемент case_data.py), color хранится с
    большой буквы ("Белое", как в исходном CSV) — фильтр обязан совпасть
    независимо от регистра ЛЮБОЙ из трёх сторон."""
    _patch_catalog(monkeypatch, [_card("w1", color="Белое"), _card("w2", color="Красное")])
    r = client.get("/v1/catalog?color=белое", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["w1"]


def test_sugar_filter_excludes_wines_with_unknown_sugar_not_crashes(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("no-sugar-info", sugar=None), _card("known", sugar="сухое")])
    r = client.get("/v1/catalog?sugar=сухое", headers=_auth(client))
    assert r.status_code == 200
    assert [w["wine_id"] for w in r.json()["wines"]] == ["known"]


def test_filters_combine_with_search(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [
        _card("a", name="Каберне", color="красное"),
        _card("b", name="Каберне", color="белое"),
    ])
    r = client.get("/v1/catalog?q=каберне&color=красное", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["a"]


# --- неизвестные/некорректные параметры не роняют ---------------------------

def test_unknown_query_params_are_ignored_not_500(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1")])
    r = client.get("/v1/catalog?bogus=1&another=xyz", headers=_auth(client))
    assert r.status_code == 200
    assert len(r.json()["wines"]) == 1


def test_out_of_range_limit_and_offset_are_422_validation_error_not_crash(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card("w1")])
    headers = _auth(client)
    for query in ("limit=0", "limit=101", "offset=-1"):
        r = client.get(f"/v1/catalog?{query}", headers=headers)
        assert r.status_code == 422, query
        assert r.json()["error"]["code"] == "validation_error"


# --- порядок: детерминированный, имя+тай-брейк по слагу ---------------------

def test_order_is_deterministic_across_repeated_calls(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [_card(f"w{i}", name=f"Вино {i}") for i in range(10)])
    headers = _auth(client)
    r1 = client.get("/v1/catalog", headers=headers).json()
    r2 = client.get("/v1/catalog", headers=headers).json()
    assert r1 == r2


def test_order_sorted_by_name_then_wine_id_as_tie_break(client: TestClient, monkeypatch):
    _patch_catalog(monkeypatch, [
        _card("z-slug", name="Абрикос"),
        _card("a-slug", name="Абрикос"),  # то же имя -> тай-брейк по слагу
        _card("b-slug", name="Яблоко"),
    ])
    r = client.get("/v1/catalog", headers=_auth(client))
    assert [w["wine_id"] for w in r.json()["wines"]] == ["a-slug", "z-slug", "b-slug"]
