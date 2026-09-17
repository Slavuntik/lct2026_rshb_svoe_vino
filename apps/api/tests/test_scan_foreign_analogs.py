"""agents/B7-foreign-analogs.md: фолбэк POST /v1/scan/resolve на пустых
matches — токен сорта/стиля из pipeline/ref -> тот же резолвер стиля, что и
/v1/analogs -> аналоги.

_FakeForeignRetriever ниже — НЕ дублёр логики резолвера (та не переписана, а
вызывается — app/foreign_scan_lookup.py::resolve_foreign_analogs), а лёгкий
дублёр ДАННЫХ: 6-стилевой набор app/rag/fixtures.py (MockRetriever) — под
/v1/analogs-тесты и не знает ни "рислинг", ни "санджовезе" (это срез
pipeline/ref, отдельный от RAG-фикстур контур). Подмена app.state.retriever —
тот же приём, что test_scan_photo.py использует для app.state.image_index.
"""
from __future__ import annotations

from starlette.testclient import TestClient

from app.rag.interface import Candidate
from tests.conftest import auth_header, make_guest


class _FakeForeignRetriever:
    """Только то, что зовёт фолбэк: resolve_label (пусто -> живой кейс B7),
    resolve_style (грубый contains-фаззи — не сам матчер под тестом),
    analog_for_style (готовые кандидаты)."""

    def __init__(self, styles: dict[str, dict], wines_by_style: dict[str, list[dict]]) -> None:
        self._styles = styles
        self._wines_by_style = wines_by_style

    def resolve_label(self, text: str, hints: dict | None = None) -> list[Candidate]:
        return []

    def resolve_style(self, query: str) -> dict | None:
        q = query.strip().lower()
        for slug, style in self._styles.items():
            if q == slug.lower() or q in style["name"].lower():
                return style
        return None

    def analog_for_style(self, style_slug: str, *, filters=None, top_k: int = 12) -> list[Candidate]:
        wines = self._wines_by_style.get(style_slug, [])
        return [
            Candidate(id=w["wine_id"], kind="wine", score=1.0, text="", url="https://example.com",
                      meta={"source": w, "derived": {}})
            for w in wines[:top_k]
        ]


def _install_foreign_retriever(app) -> None:
    app.state.retriever = _FakeForeignRetriever(
        styles={
            "riesling-trocken": {"slug": "riesling-trocken", "name": "Рислинг сухой", "country": "Германия"},
            "chianti-classico": {"slug": "chianti-classico", "name": "Кьянти Классико", "country": "Италия"},
        },
        wines_by_style={
            "riesling-trocken": [{
                "wine_id": "krym-riesling-suhoy", "name": "Крымский Рислинг сухой",
                "winery_name": "Легенда Крыма", "region_name": "Крым",
            }],
            "chianti-classico": [{
                "wine_id": "don-krasnostop", "name": "Донской Красностоп",
                "winery_name": "Винодельня Дона", "region_name": "Дон",
            }],
        },
    )


def test_scan_resolve_urban_risling_falls_back_to_style_analogs(client: TestClient, app):
    """Живой кейс Вячеслава (17.09): "Urban Risling" -> 0 совпадений в
    каталоге, но опечатка сорта распознаётся (risling~riesling) -> аналоги."""
    _install_foreign_retriever(app)
    tokens = make_guest(client)
    r = client.post("/v1/scan/resolve", json={"text": "Urban Risling"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["matches"] == []
    assert body["low_confidence"] is True
    assert len(body["analogs"]) == 1
    assert body["analogs"][0]["wine_id"] == "krym-riesling-suhoy"
    assert body["analog_reason"] is not None and "рислинг" in body["analog_reason"].lower()
    assert "Urban Risling" in body["analog_reason"]


def test_scan_resolve_chianti_falls_back_to_sangiovese(client: TestClient, app):
    """"Chianti" сам не сорт, а аппелласьон (reference_styles.yaml: grapes:
    [Санджовезе]) — токен приходит из стилевого, не сортового индекса."""
    _install_foreign_retriever(app)
    tokens = make_guest(client)
    r = client.post("/v1/scan/resolve", json={"text": "Chianti"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert len(body["analogs"]) == 1
    assert body["analogs"][0]["wine_id"] == "don-krasnostop"
    assert body["analog_reason"] is not None and "санджовезе" in body["analog_reason"].lower()


def test_scan_resolve_gibberish_has_no_analogs(client: TestClient, app):
    """0 совпадений в каталоге (форсировано фейковым ретривером — настоящий
    MockRetriever на "абракадабре" отдаёт немного шума через rapidfuzz
    token_set_ratio даже без единого осмысленного слова — 6 фикстур
    короткие, это особенность самого resolve_label, не в фокусе фолбэка) —
    и в "абракадабре" нет токена сорта/стиля (pipeline/ref, реальные
    справочники): фолбэк честно ничего не находит, без 404/500."""
    _install_foreign_retriever(app)
    tokens = make_guest(client)
    r = client.post("/v1/scan/resolve", json={"text": "абракадабра"}, headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["matches"] == []
    assert body["analogs"] == []
    assert body["analog_reason"] is None


def test_scan_resolve_good_match_ignores_fallback_even_if_text_names_a_grape(client: TestClient):
    """Регрессия (брифа п.4): непустые matches -> фолбэк НЕ вызывается вовсе,
    даже когда текст буквально содержит узнаваемый сорт ("Каберне Совиньон")
    — существующее поведение полных совпадений не меняется ни на йоту."""
    tokens = make_guest(client)
    r = client.post("/v1/scan/resolve", json={"text": "Шато Вымысел Каберне Совиньон"},
                     headers=auth_header(tokens))
    assert r.status_code == 200
    body = r.json()
    assert body["matches"], "прямое совпадение в каталоге обязано найтись, как и до B7"
    assert body["low_confidence"] is False
    assert body["analogs"] == []
    assert body["analog_reason"] is None
