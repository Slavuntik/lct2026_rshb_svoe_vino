"""GET /v1/healthz — v0.4.4 (ревью 04, блокер 2) добавила поле `warm`;
v0.4.7 (контракт §6, TODO-3 ревью 05) развела версии по неймспейсам:
rag_index_version + cv_index_version, `index_version` остался
DEPRECATED-алиасом rag_index_version (до v0.5)."""
from __future__ import annotations

from starlette.testclient import TestClient

from app.cv.interface import Match


def test_healthz_reports_ok_status_and_namespaced_index_versions(client: TestClient):
    r = client.get("/v1/healthz")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "index_version", "rag_index_version", "cv_index_version", "warm"}
    assert body["status"] == "ok"
    assert body["index_version"]
    assert body["cv_index_version"]
    # v0.4.7: index_version — ровно deprecated-алиас rag_index_version, не
    # какое-то третье собственное значение.
    assert body["index_version"] == body["rag_index_version"]


def test_healthz_rag_and_cv_versions_are_independent_namespaces(client: TestClient, app):
    """Ревью 05: раньше единственное поле `index_version` смотрело ТОЛЬКО на
    RAG-ретривер — на RAG_PROVIDER=mock оно молчаливо создавало впечатление
    "CV тоже на моке", даже когда реальный CV-индекс работал (ложная тревога
    прогона B3, reports/05-dataset-wave.md). Разведение версий обязано
    показать РАЗНЫЕ значения, когда они реально разные — не совпадать по
    случайности фикстур."""

    class _StubImageIndex:
        index_version = "cv-stub-v9"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return []

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _StubImageIndex()
    body = client.get("/v1/healthz").json()
    assert body["cv_index_version"] == "cv-stub-v9"
    assert body["rag_index_version"] != "cv-stub-v9"
    assert body["index_version"] == body["rag_index_version"]


def test_healthz_cv_index_version_falls_back_to_unknown_sentinel_not_null(client: TestClient, app):
    """openapi.yaml объявляет cv_index_version строкой (не nullable) —
    реальный cv.index.ImageIndex.index_version честно отдаёт None, пока
    манифеста ещё нет (индекс не строился, app/cv/interface.py). healthz
    обязан коалесцировать это в тот же "unknown"-сентинел, что и у rag,
    а не протащить null в контрактно-строковое поле."""

    class _NoManifestImageIndex:
        index_version = None

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return []

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _NoManifestImageIndex()
    body = client.get("/v1/healthz").json()
    assert body["cv_index_version"] == "unknown"


def test_healthz_warm_is_true_on_default_mock_image_provider(client: TestClient):
    """IMAGE_PROVIDER=mock/VERIFIER_PROVIDER=mock (дефолт) — нечего греть,
    warm=True сразу, без обращения к настоящим энкодеру/OCR."""
    r = client.get("/v1/healthz")
    assert r.json()["warm"] is True


def test_healthz_warm_is_and_of_image_index_and_label_verifier_warmup(client: TestClient, app):
    """v0.4.7 (TODO-1 ревью 05): warm теперь обязан честно отражать ОБА
    прогрева (энкодер + верификатор) — сервис не "тёплый", если хотя бы один
    не прогрелся, даже если другой в порядке."""
    app.state.image_index_warm = True
    app.state.label_verifier_warm = False
    assert client.get("/v1/healthz").json()["warm"] is False

    app.state.image_index_warm = False
    app.state.label_verifier_warm = True
    assert client.get("/v1/healthz").json()["warm"] is False

    app.state.image_index_warm = True
    app.state.label_verifier_warm = True
    assert client.get("/v1/healthz").json()["warm"] is True
