"""CV_FUSION (agents/G7-text-fusion.md) — боевое слияние CV (кроп+весь кадр) +
текстовый поиск по всему каталогу кейса, в apps/api/app/cv/service.py.

Тестовые дубли `image_index`/`label_verifier` — тот же приём, что test_scan_photo.py
(классы прямо в тесте, не мок cv.index.ImageIndex целиком) — `image_index` здесь
дополнительно несёт `search_fusion()` (новый метод CV_FUSION), `search()` остаётся
только для проверки "не вызывается вовсе, когда слияние решает" (текст-ранк/near-dup
пути). Текстовый индекс слияния строится из СВОЕГО временного CSV (`CV_CASE_CATALOG_CSV`)
— не от боевого `case-data/strapi_output0709.csv` (детерминизм, изоляция от машины).
"""
from __future__ import annotations

import csv
import dataclasses
import json

from starlette.testclient import TestClient

from app.cv.interface import Match
from tests.conftest import auth_header, register_user
from tests.test_scan_photo import _photo, _SpyLabelVerifier

_CSV_FIELDS = [
    "Название вина", "Категория", "Цвет", "Регион", "Сорт винограда",
    "Описание", "Винодельня", "Slug", "Название фото",
]


def _write_fusion_catalog_csv(tmp_path, rows: list[dict[str, str]]):
    path = tmp_path / "fusion_catalog.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _enable_fusion(
    app, tmp_path, monkeypatch, *, catalog_rows=None, families=None, winery_aliases=None, **overrides,
):
    """Общая обвязка: включает CV_FUSION, изолирует текстовый индекс/перепись
    семей от боевого case-data (детерминизм), опционально задаёт остальные
    settings полем `overrides` (dataclasses.replace).

    `winery_aliases` (agents/ML-1-*.md, задача 2) — как `families`: словарь
    `{"groups": [...]}` (см. `cv.text_fusion.load_winery_alias_groups`),
    записывается в `<tmp_path>/winery_aliases.json` и включается через
    `CV_WINERY_ALIASES_JSON`. Не передан -> путь по умолчанию
    (`$CASE_DATA_DIR/winery_aliases.json`, т.е. `tmp_path/winery_aliases.json`)
    просто не существует — `_fusion_winery_index()` честно деградирует к 1:1."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))  # пусто — card/candidates честно деградируют на RAG-мок
    csv_path = _write_fusion_catalog_csv(tmp_path, catalog_rows or [])
    monkeypatch.setenv("CV_CASE_CATALOG_CSV", str(csv_path))
    if families is not None:
        fam_path = tmp_path / "families.json"
        fam_path.write_text(json.dumps(families, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("CV_FAMILIES_JSON", str(fam_path))
    if winery_aliases is not None:
        aliases_path = tmp_path / "winery_aliases.json"
        aliases_path.write_text(json.dumps(winery_aliases, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv("CV_WINERY_ALIASES_JSON", str(aliases_path))
    app.state.settings = dataclasses.replace(app.state.settings, cv_fusion=True, **overrides)
    # lru_cache модулей слияния — сбрасываем между тестами, ключ (str-путь) и так
    # уникален per-tmp_path, но подчищаем явно ради безопасности при повторных путях.
    from app.cv.service import _fusion_colors, _fusion_family_by_slug, _fusion_text_index, _fusion_winery_index
    _fusion_text_index.cache_clear()
    _fusion_family_by_slug.cache_clear()
    _fusion_colors.cache_clear()
    _fusion_winery_index.cache_clear()


class _FusionImageIndex:
    """`search_fusion()` возвращает ФИКСИРОВАННЫЙ список `Match` независимо от
    `top_k`/`extra_slugs` (те очевидны из вызова — см. `calls` ниже, если тест
    должен свериться с аргументами)."""

    index_version = "fusion-stub"

    def __init__(self, fusion_matches: list[Match], plain_matches: list[Match] | None = None):
        self._fusion_matches = fusion_matches
        self._plain_matches = plain_matches if plain_matches is not None else fusion_matches
        self.search_calls = 0
        self.search_fusion_calls: list[dict] = []

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        self.search_calls += 1
        return self._plain_matches

    def embed_fusion_query(self, image: bytes, *, normalize: bool = True):
        if not image or image.startswith(b"not an image"):
            raise ValueError("битые байты")
        self.embed_fusion_calls = getattr(self, "embed_fusion_calls", 0) + 1
        return [1.0], [0.0]

    def search_fusion(self, image: bytes | None, *, top_k: int = 50, extra_slugs=(), vectors=None) -> list[Match]:
        self.search_fusion_calls.append({"top_k": top_k, "extra_slugs": list(extra_slugs), "vectors": vectors})
        return self._fusion_matches

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def _m(slug: str, score: float) -> Match:
    return Match(slug=slug, score=score, gap=None, view="real")


# --------------------------------------------------------------------------------------
# Дефолты — пин (agents/G7-text-fusion.md, "Параметры")
# --------------------------------------------------------------------------------------


def test_default_cv_fusion_settings_are_off_with_brief_values():
    from app.config import Settings
    settings = Settings()
    assert settings.cv_fusion is False
    assert settings.cv_fusion_verify is False
    assert settings.cv_fusion_w == 0.2
    assert settings.cv_fusion_gap_floor == 0.03
    assert settings.cv_fusion_cv_floor == 0.80


# --------------------------------------------------------------------------------------
# Выключено (дефолт) — byte-for-byte ПРЕЖНИЙ путь, search_fusion не трогается
# --------------------------------------------------------------------------------------


def test_fusion_disabled_never_calls_search_fusion_or_reads_ocr(client: TestClient, app):
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.95)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(ocr_text="что угодно")
    app.state.label_verifier = spy
    assert app.state.settings.cv_fusion is False  # дефолт, явно не трогаем

    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 1
    assert idx.search_fusion_calls == []
    assert spy.read_query_text_calls == 0


# --------------------------------------------------------------------------------------
# Включено: search_fusion вызывается (не search), OCR ровно один раз
# --------------------------------------------------------------------------------------


def test_fusion_enabled_calls_search_fusion_not_plain_search(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex(
        fusion_matches=[_m("target-wine", 0.90)],
        plain_matches=[_m("should-never-be-chosen", 0.99)],
    )
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=[
        {"Slug": "target-wine", "Название вина": "Целевое вино", "Винодельня": "В"},
    ])

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    assert idx.search_calls == 0, "CV_FUSION обязан полностью обойти обычный search()"
    assert len(idx.search_fusion_calls) == 1
    assert r.json()["matches"][0]["slug"] == "target-wine"


def test_fusion_reads_ocr_exactly_once_per_request(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([_m("a", 0.90), _m("b", 0.5)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(ocr_text="фанагория")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    assert spy.read_query_text_calls == 1


# --------------------------------------------------------------------------------------
# Итоговый скор = cv + w*rel; top1_score/gap — из НЕ-blended CV-компоненты
# --------------------------------------------------------------------------------------


def test_fusion_final_score_uses_text_relevance_and_top1_score_stays_cv_only(
    client: TestClient, app, tmp_path, monkeypatch
):
    """brief п.3: matches[i].score = final (cv+w*rel), НО confidence.top1_score
    — сырой CV-скор top-1 (не blended) — те же два разных числа, что видно в
    ответе API."""
    idx = _FusionImageIndex([_m("wine-a", 0.85), _m("wine-b", 0.83)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="УНИКАЛЬНЫЙ ТОКЕН ВИНО-А")
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=[
        {"Slug": "wine-a", "Название вина": "Уникальный Токен Вино-А", "Винодельня": "Погреб"},
        {"Slug": "wine-b", "Название вина": "Совсем другое", "Винодельня": "Иное"},
    ], cv_fusion_w=0.2)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    match_a = next(m for m in body["matches"] if m["slug"] == "wine-a")
    assert match_a["score"] > 0.85 + 1e-9, "final обязан включать w*rel > 0, не остаться голым CV"
    assert body["confidence"]["top1_score"] == 0.85, "top1_score — сырой CV, а не final"


def test_fusion_promotes_text_matched_candidate_above_higher_cv_rival(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Главный DoD брифа: верный слаг ниже по CV, но текст его узнаёт -> он
    обязан подняться на top-1 итогового ранжирования."""
    idx = _FusionImageIndex([_m("cv-favorite", 0.80), _m("text-favorite", 0.75)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="ФАНАГОРИЯ КРЮ ЛЕРМОНТ")
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=[
        {"Slug": "text-favorite", "Название вина": "Фанагория Крю Лермонт", "Винодельня": "Фанагория"},
        {"Slug": "cv-favorite", "Название вина": "Нечто иное", "Винодельня": "Другое"},
    ], cv_fusion_w=0.2)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.json() == {"slug": "text-favorite"}


# --------------------------------------------------------------------------------------
# Гейт: cv_floor И gap_floor (AND), независимые пороги от CV_ABS_FLOOR/CV_MARGIN_FLOOR
# --------------------------------------------------------------------------------------


def test_fusion_gate_confident_when_both_floors_pass(client: TestClient, app, tmp_path, monkeypatch):
    # "shato-vymysel-cabernet" — известный слаг мок-RAG (app/rag/fixtures.py),
    # нужен только чтобы card честно построилась (не сама цель этого теста).
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_cv_floor=0.80, cv_fusion_gap_floor=0.03)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == "shato-vymysel-cabernet"
    assert body["card"] is not None


def test_fusion_gate_not_confident_when_cv_floor_fails_despite_wide_gap(
    client: TestClient, app, tmp_path, monkeypatch
):
    idx = _FusionImageIndex([_m("winner", 0.50), _m("far-rival", 0.10)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_cv_floor=0.80, cv_fusion_gap_floor=0.03)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["confidence"]["top1_score"] == 0.50


def test_fusion_gate_not_confident_when_gap_fails_despite_high_cv(
    client: TestClient, app, tmp_path, monkeypatch
):
    # известные слаги мок-RAG — нужны, чтобы `similar` честно заполнился ниже
    # (_wine_item читает ТОЛЬКО retriever.get_by_id, без case-data фолбэка,
    # в отличие от `candidates` — существующее поведение, не про слияние).
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("tihaya-gavan-pinot-noir", 0.895)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(
        app, tmp_path, monkeypatch,
        families={"fam9": {"slugs": ["unrelated-a", "unrelated-b"]}},  # оба слага — не в переписи
        cv_fusion_cv_floor=0.80, cv_fusion_gap_floor=0.03,
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["confidence"]["gap"] < 0.03
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["similar"]  # честная деградация — кандидаты видны


def test_fusion_gate_null_gap_dominance_needs_only_cv_floor(client: TestClient, app, tmp_path, monkeypatch):
    """Единственный кандидат вселенной -> gap=None (доминирование) — confident
    зависит только от cv_floor, ровно как v0.4.7 трактует null-gap для
    обычного (не fusion) гейта."""
    idx = _FusionImageIndex([_m("solo", 0.90)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_cv_floor=0.80, cv_fusion_gap_floor=0.03)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["confidence"]["gap"] is None
    assert body["not_in_catalog"] is False
    assert body["slug"] == "solo"


def test_fusion_returns_not_in_catalog_when_search_fusion_finds_nothing(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["matches"] == []
    assert body["candidates"] == []


def test_fusion_flat_mode_always_returns_best_guess_even_when_not_confident(
    client: TestClient, app, tmp_path, monkeypatch
):
    idx = _FusionImageIndex([_m("best-guess", 0.40)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_cv_floor=0.80)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.json() == {"slug": "best-guess"}


# --------------------------------------------------------------------------------------
# CV_FUSION_VERIFY: near-dup OCR-верификатор поверх слияния, выключен по умолчанию
# --------------------------------------------------------------------------------------


def test_fusion_verify_off_by_default_even_with_close_candidates(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([_m("a", 0.90), _m("b", 0.895)])  # в пределах cv_verify_proximity(0.04)
    app.state.image_index = idx
    spy = _SpyLabelVerifier(answer="b", ocr_text="что-то")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch)  # cv_fusion_verify не передан -> дефолт False
    assert app.state.settings.cv_fusion_verify is False

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert spy.calls == [], "CV_FUSION_VERIFY выключен — verify() не должен вызываться вовсе"
    assert r.json()["ocr_verified"] is False


def test_fusion_verify_on_calls_verifier_for_close_candidates_and_applies_answer(
    client: TestClient, app, tmp_path, monkeypatch
):
    idx = _FusionImageIndex([_m("ann-top1", 0.90), _m("ocr-pick", 0.895)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(answer="ocr-pick", ocr_text="2022")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_verify=True)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert spy.calls, "оба кандидата в пределах cv_verify_proximity — verify() обязан вызваться"
    called_slugs = {c["slug"] for c in spy.calls[0]}
    assert called_slugs == {"ann-top1", "ocr-pick"}
    assert spy.ocr_texts == ["2022"], "verify() обязан получить УЖЕ прочитанный OCR-текст, не читать заново"
    assert body["ocr_verified"] is True
    assert body["slug"] == "ocr-pick"


def test_fusion_verify_swap_does_not_change_top1_score_or_gap(client: TestClient, app, tmp_path, monkeypatch):
    """top1_score/gap — от ДОРЕВERIFY топа слияния (ranked_top[0]), не от
    финального выбора верификатора — та же дисциплина, что и путь без слияния
    (см. test_scan_photo.py::test_near_dup_top_candidates_trigger_ocr_verification
    контекст)."""
    idx = _FusionImageIndex([_m("ann-top1", 0.90), _m("ocr-pick", 0.895)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(answer="ocr-pick", ocr_text="2022")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_verify=True)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["slug"] == "ocr-pick"
    assert body["confidence"]["top1_score"] == 0.90  # ann-top1's cv, не ocr-pick's


def test_fusion_verify_abstention_without_ocr_bypass_is_honestly_not_in_catalog(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Зеркало test_near_dup_ocr_failure_is_honestly_not_in_catalog (без
    слияния): OCR воздержался (answer=None) -> ocr_verified=False -> gap-обход
    не срабатывает -> gap=0.005 < CV_FUSION_GAP_FLOOR(0.03) проваливает гейт
    честно, а не тихо остаётся confident на ANN top-1. flat, тем не менее,
    обязан продолжать отдавать лучший угад (best_guess_slug не зависит от гейта)."""
    idx = _FusionImageIndex([_m("ann-top1", 0.90), _m("close-rival", 0.895)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(answer=None, ocr_text="нечитаемо")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_verify=True)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert spy.calls
    assert body["ocr_verified"] is False
    assert body["not_in_catalog"] is True
    assert body["slug"] is None

    flat = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert flat.json() == {"slug": "ann-top1"}


def test_fusion_verify_not_called_when_dominant_top_has_no_close_neighbors(
    client: TestClient, app, tmp_path, monkeypatch
):
    idx = _FusionImageIndex([_m("clearly-the-one", 0.97), _m("distant", 0.30)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(ocr_text="")
    app.state.label_verifier = spy
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_verify=True)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert spy.calls == []
    assert r.json()["slug"] == "clearly-the-one"


# --------------------------------------------------------------------------------------
# CV_FUSION + CV_TEXT_RERANK одновременно — слияние побеждает целиком (brief задача 3)
# --------------------------------------------------------------------------------------


def test_fusion_takes_precedence_over_text_rerank_when_both_enabled(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex(
        fusion_matches=[_m("fusion-pick", 0.90)],
        plain_matches=[_m("would-be-rerank-pick", 0.99)],
    )
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_text_rerank=True)
    assert app.state.settings.cv_text_rerank is True and app.state.settings.cv_fusion is True

    r = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert r.json() == {"slug": "fusion-pick"}
    assert idx.search_calls == 0, "text_rerank читает через search() — слияние обязано его не вызывать вовсе"


# --------------------------------------------------------------------------------------
# candidates: обогащены карточкой, score = final (тот же список, что matches)
# --------------------------------------------------------------------------------------


def test_fusion_candidates_field_uses_final_score_and_aligns_with_matches(
    client: TestClient, app, tmp_path, monkeypatch
):
    idx = _FusionImageIndex([_m("wine-a", 0.85), _m("wine-b", 0.10)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch)

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert [c["wine_id"] for c in body["candidates"]] == [m["slug"] for m in body["matches"]]
    assert [c["score"] for c in body["candidates"]] == [m["score"] for m in body["matches"]]
