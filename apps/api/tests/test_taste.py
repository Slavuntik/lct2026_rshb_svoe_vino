from __future__ import annotations

from starlette.testclient import TestClient

from tests.conftest import auth_header, make_guest, register_user


def test_swipes_require_profiling_scope_for_registered_user(client: TestClient):
    tokens = register_user(client, email="t1@example.com", scopes=["base"])  # без profiling
    r = client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                     headers=auth_header(tokens))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"


def test_guest_never_gets_profiling_even_if_it_tries_to_grant_it(client: TestClient):
    tokens = make_guest(client)
    headers = auth_header(tokens)
    # Гость технически может дёрнуть /consents и попытаться выдать себе profiling...
    client.post("/v1/consents", json={
        "consent_version": "v1", "grant": True, "scopes": ["profiling"],
    }, headers=headers)
    # ...но require_profiling_consent всё равно блокирует любого guest-принципала.
    r = client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                     headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"


def test_swipe_then_profile_reflects_liked_wine_sensory_vector(client: TestClient):
    tokens = register_user(client, email="t2@example.com", scopes=["base", "profiling"])
    headers = auth_header(tokens)

    r = client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                     headers=headers)
    assert r.status_code == 204

    r = client.get("/v1/taste/profile", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["swipes_count"] == 1
    assert body["top_styles"] == ["valpolicella"]
    # sensory той самой фикстуры: acidity 0.55
    assert abs(body["vector"]["acidity"] - 0.55) < 1e-6


def test_profile_defaults_to_neutral_vector_before_any_swipe(client: TestClient):
    tokens = register_user(client, email="t3@example.com", scopes=["base", "profiling"])
    r = client.get("/v1/taste/profile", headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["swipes_count"] == 0
    assert body["top_styles"] == []
    assert all(v == 0.5 for v in body["vector"].values())


def test_dislikes_do_not_pull_vector_but_count_towards_swipes_count(client: TestClient):
    tokens = register_user(client, email="t4@example.com", scopes=["base", "profiling"])
    headers = auth_header(tokens)
    client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "dislike"},
                headers=headers)
    r = client.get("/v1/taste/profile", headers=headers)
    body = r.json()
    assert body["swipes_count"] == 1
    assert all(v == 0.5 for v in body["vector"].values())  # ни одного like -> нейтрально


# --- GET /taste/candidates (v0.2.2, пробел агента C) ------------------------

def test_candidates_requires_profiling_scope_guest_gets_403(client: TestClient):
    tokens = make_guest(client)
    r = client.get("/v1/taste/candidates", headers=auth_header(tokens))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"


def test_candidates_requires_profiling_scope_user_without_it_gets_403(client: TestClient):
    tokens = register_user(client, email="cand1@example.com", scopes=["base"])
    r = client.get("/v1/taste/candidates", headers=auth_header(tokens))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "consent_required"


def test_candidates_returns_wines_with_contract_shape(client: TestClient):
    tokens = register_user(client, email="cand2@example.com", scopes=["base", "profiling"])
    r = client.get("/v1/taste/candidates", headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["wines"], "на 6 вымышленных винах и без свайпов колода не должна быть пустой"
    first = body["wines"][0]
    assert set(["wine_id", "name", "winery_name"]).issubset(first.keys())
    assert all("wine_id" in w and "name" in w and "winery_name" in w for w in body["wines"])


def test_candidates_excludes_already_swiped_wines_regardless_of_verdict(client: TestClient):
    tokens = register_user(client, email="cand3@example.com", scopes=["base", "profiling"])
    headers = auth_header(tokens)

    client.post("/v1/taste/swipes", json={"wine_id": "shato-vymysel-cabernet", "verdict": "like"},
                headers=headers)
    client.post("/v1/taste/swipes", json={"wine_id": "rozovyy-mirazh", "verdict": "skip"},
                headers=headers)

    r = client.get("/v1/taste/candidates", headers=headers)
    ids = {w["wine_id"] for w in r.json()["wines"]}
    assert "shato-vymysel-cabernet" not in ids
    assert "rozovyy-mirazh" not in ids


def test_candidates_respects_limit_query_param(client: TestClient):
    tokens = register_user(client, email="cand4@example.com", scopes=["base", "profiling"])
    r = client.get("/v1/taste/candidates?limit=2", headers=auth_header(tokens))
    assert r.status_code == 200
    assert len(r.json()["wines"]) <= 2


def test_candidates_limit_over_50_is_rejected(client: TestClient):
    tokens = register_user(client, email="cand5@example.com", scopes=["base", "profiling"])
    r = client.get("/v1/taste/candidates?limit=51", headers=auth_header(tokens))
    assert r.status_code == 422  # validation_error по pydantic/query-constraint


def test_candidates_returns_empty_once_everything_swiped(client: TestClient):
    from app.rag.fixtures import WINES

    tokens = register_user(client, email="cand6@example.com", scopes=["base", "profiling"])
    headers = auth_header(tokens)
    for wine in WINES:
        client.post("/v1/taste/swipes", json={"wine_id": wine["slug"], "verdict": "skip"}, headers=headers)

    r = client.get("/v1/taste/candidates", headers=headers)
    assert r.status_code == 200
    assert r.json()["wines"] == []
