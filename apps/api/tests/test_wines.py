from __future__ import annotations

import json

from starlette.testclient import TestClient

from tests.conftest import auth_header, register_user


def test_wine_card_shape_matches_contract(client: TestClient):
    tokens = register_user(client, email="w1@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert set(["wine_id", "source", "derived", "source_url"]).issubset(body.keys())
    assert body["wine_id"] == "shato-vymysel-cabernet"
    assert body["source"]["name"] == "Шато Вымысел Каберне Совиньон"
    assert "sensory" in body["derived"]
    assert body["source_url"].startswith("https://")
    # source не должен дублировать derived/slug/source_url внутри себя
    assert "derived" not in body["source"]
    assert "source_url" not in body["source"]


def test_wine_card_includes_similar(client: TestClient):
    tokens = register_user(client, email="w2@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    assert "tihaya-gavan-pinot-noir" in r.json()["similar"]


# --- v0.3.6 (architect, contracts/openapi.yaml — дефект жюри, reports/
# qa-manual-hack-v16.md п.5.1): similar_wines — {wine_id,name,winery,
# image_url} по тем же слагам/порядку, что similar. -----------------------

def test_wine_card_similar_wines_are_enriched_with_name_winery_image(client: TestClient):
    tokens = register_user(client, email="w-similar-enriched@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    body = r.json()
    assert body["similar"] == ["tihaya-gavan-pinot-noir"]
    assert body["similar_wines"] == [{
        "wine_id": "tihaya-gavan-pinot-noir",
        "name": "Тихая Гавань Пино Нуар",
        "winery": "Гавань Эстейт",
        "image_url": "https://example.com/mock-catalog/img/tihaya-gavan-pinot-noir.webp",
    }]


def test_wine_card_similar_wines_preserve_order(client: TestClient):
    """belye-peski-sauvignon-blanc — единственное вино фикстур, чей similar()
    детерминированно отдаёт ДВА кандидата (оба "белое", без явного
    similar_wine_slugs) в порядке перечисления WINES (app/rag/fixtures.py):
    igristoe-nebo-brut, затем sladkiy-zakat-muskat — хороший регресс на
    "тот же порядок, что similar"."""
    tokens = register_user(client, email="w-similar-order@example.com")
    r = client.get("/v1/wines/belye-peski-sauvignon-blanc", headers=auth_header(tokens))
    body = r.json()
    assert body["similar"] == ["igristoe-nebo-brut", "sladkiy-zakat-muskat"]
    assert [w["wine_id"] for w in body["similar_wines"]] == ["igristoe-nebo-brut", "sladkiy-zakat-muskat"]


def test_wine_card_similar_wines_empty_when_similar_empty(client: TestClient):
    """rozovyy-mirazh — единственное "розовое" вино фикстур и
    similar_wine_slugs=[] — similar() честно отдаёт [], similar_wines тоже []
    (не None, не ошибка)."""
    tokens = register_user(client, email="w-similar-empty@example.com")
    r = client.get("/v1/wines/rozovyy-mirazh", headers=auth_header(tokens))
    body = r.json()
    assert body["similar"] == []
    assert body["similar_wines"] == []


def test_wine_card_similar_wines_skips_slug_without_usable_name(client: TestClient, app, monkeypatch):
    """Слаг в similar, чей источник не даёт пригодного name (тот же класс
    пробела в каталоге, что хотфикс AnalogsWineItem в routers/scan.py) —
    пропущен в similar_wines, но остаётся в similar; ответ честно 200, не 500."""
    from app.rag.interface import Candidate

    real_similar = app.state.retriever.similar

    def fake_similar(wine_id: str, top_k: int = 6):
        candidates = list(real_similar(wine_id, top_k=top_k))
        candidates.append(Candidate(
            id="ghost-wine-no-name", kind="wine", score=0.5, text="",
            url="https://example.com/ghost", meta={"source": {"name": ""}, "derived": {}},
        ))
        return candidates

    monkeypatch.setattr(app.state.retriever, "similar", fake_similar)

    tokens = register_user(client, email="w-similar-ghost@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert "ghost-wine-no-name" in body["similar"]
    assert all(w["wine_id"] != "ghost-wine-no-name" for w in body["similar_wines"])


def test_unknown_wine_id_is_404_not_found(client: TestClient):
    tokens = register_user(client, email="w3@example.com")
    r = client.get("/v1/wines/does-not-exist-at-all", headers=auth_header(tokens))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_wine_card_requires_auth(client: TestClient):
    r = client.get("/v1/wines/shato-vymysel-cabernet")
    assert r.status_code == 401


# --- v0.4.11 (агент B8): фолбэк на каталог кейса, когда RAG не знает slug ---

def _write_case_catalog(tmp_path, mapping: dict) -> None:
    (tmp_path / "case_catalog.json").write_text(
        json.dumps({"mapping": mapping}, ensure_ascii=False), encoding="utf-8"
    )


def test_wine_card_falls_back_to_case_catalog_when_rag_does_not_know_slug(
    client: TestClient, tmp_path, monkeypatch
):
    """Слаг НАРОЧНО отсутствует в app/rag/fixtures.py::WINES — воспроизводит
    ровно сценарий контракта ("122 из 2054 слагов кейса отсутствуют в нашем
    RAG-каталоге"). Форма ответа обязана остаться WineResponse буквально
    (source/derived/source_url/similar), только источник данных — каталог
    кейса; derived/similar у фолбэка пусты (нет сенсорики/связей)."""
    _write_case_catalog(tmp_path, {
        "case-massandra-muskatel-belyy": {
            "name": "Мускатель белый", "winery_name": "Массандра", "region_name": "Крым",
            "grapes": ["Мускат белый"], "color": "Белое", "category": "Золотистый",
            "description": "Десертное крепкое вино.",
        },
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="case-fallback@example.com")
    r = client.get("/v1/wines/case-massandra-muskatel-belyy", headers=auth_header(tokens))

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"wine_id", "source", "derived", "source_url", "similar", "similar_wines"}
    assert body["wine_id"] == "case-massandra-muskatel-belyy"
    assert body["source"] == {
        "name": "Мускатель белый", "winery_name": "Массандра", "region_name": "Крым",
        "grapes": ["Мускат белый"], "color": "Белое", "category": "Золотистый",
        "description": "Десертное крепкое вино.",
        "image_url": "/v1/case-thumbs/case-massandra-muskatel-belyy.webp",
    }
    assert body["derived"] == {}
    assert body["similar"] == []
    assert body["similar_wines"] == []
    # contracts/image-scan.md v0.4.11 п.3.
    assert body["source_url"] == "https://vino-svoe.ru/wines/case-massandra-muskatel-belyy"


def test_wine_card_prefers_rag_over_case_catalog_when_both_know_slug(
    client: TestClient, tmp_path, monkeypatch
):
    """Контракт: фолбэк — только когда RAG НЕ резолвит. Наш каталог (полная
    сенсорика/similar) не должен уступать каталогу кейса (голые поля), даже
    если оба почему-то знают один и тот же slug."""
    _write_case_catalog(tmp_path, {
        "shato-vymysel-cabernet": {"name": "Это не то вино, из каталога кейса"},
    })
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    tokens = register_user(client, email="case-fallback-precedence@example.com")
    r = client.get("/v1/wines/shato-vymysel-cabernet", headers=auth_header(tokens))

    assert r.status_code == 200
    body = r.json()
    assert body["source"]["name"] == "Шато Вымысел Каберне Совиньон"  # наш RAG, не каталог кейса
    assert "sensory" in body["derived"]  # фолбэк отдал бы derived == {}


def test_unknown_wine_id_still_404_when_case_catalog_also_does_not_know_it(
    client: TestClient, tmp_path, monkeypatch
):
    """Регресс: фолбэк не должен превращать честный 404 в 200 на слаг,
    которого нет НИГДЕ (пустой CASE_DATA_DIR — не полагаемся на реальные
    данные кейса на машине, см. tests/test_case_catalog.py)."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    tokens = register_user(client, email="case-fallback-404@example.com")

    r = client.get("/v1/wines/does-not-exist-anywhere-at-all", headers=auth_header(tokens))

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_wine_card_falls_back_gracefully_without_case_data_dir(client: TestClient, tmp_path, monkeypatch):
    """CASE_DATA_DIR указывает на пустую директорию (нет case_catalog.json
    вовсе) — 404, не 500."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))
    tokens = register_user(client, email="case-fallback-empty-dir@example.com")

    r = client.get("/v1/wines/case-some-slug-not-generated-yet", headers=auth_header(tokens))

    assert r.status_code == 404
