"""POST /v1/scan/photo — кейс ЛЦТ (contracts/image-scan.md v0.4).

Задачи волны: flat-формат байт-в-байт, "always best slug" при низкой
уверенности, rich-схема, timing_ms реально измеряется, near-dup ->
OCR-верификатор.
"""
from __future__ import annotations

import dataclasses
import json
import time

from starlette.testclient import TestClient

from app.cv.interface import Match
from tests.conftest import auth_header, make_guest, register_user


def _photo(client: TestClient, payload: bytes, *, flat: bool = False, headers: dict | None = None):
    url = "/v1/scan/photo" + ("?flat=1" if flat else "")
    return client.post(url, files={"image": ("label.jpg", payload, "image/jpeg")}, headers=headers or {})


# --- flat mode: exact byte-for-byte contract, always best slug -------------

def test_flat_mode_is_exactly_one_key_slug_byte_for_byte(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=True)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # байт-в-байт: ровно {"slug": "..."} — ни одного лишнего поля.
    assert json.loads(r.text) == {"slug": "shato-vymysel-cabernet"}
    assert list(json.loads(r.text).keys()) == ["slug"]


def test_flat_mode_always_returns_best_slug_even_at_low_confidence(client: TestClient):
    """contracts/image-scan.md: "flat ВСЕГДА отдаёт лучший доступный slug,
    даже при низкой уверенности" — семантика скрипта оценки: пустой ответ
    гарантированно мимо, лучший кандидат имеет шанс."""
    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": "rozovyy-mirazh"}


def test_flat_mode_never_errors_on_empty_file(client: TestClient):
    r = _photo(client, b"", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_never_errors_on_oversized_file(client: TestClient, monkeypatch):
    # v0.4.4: лимит поднят до 25 МБ (было 8) — филлер должен реально его
    # превышать, иначе тест перестал бы проверять лимит вообще.
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet" + b"0" * (26 * 1024 * 1024), flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_never_errors_on_unrecognized_photo(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:unknown", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_sequential_posts_all_valid_json(client: TestClient):
    """DoD image-scan.md: "последовательные POST -> каждый ответ — валидный
    плоский JSON" — минимальный прогон нескольких подряд."""
    payloads = [
        b"MOCKPHOTO:shato-vymysel-cabernet", b"MOCKPHOTO:unknown", b"",
        b"MOCKPHOTO:weak:igristoe-nebo-brut", b"MOCKPHOTO:near-dup",
    ]
    for payload in payloads:
        r = _photo(client, payload, flat=True)
        assert r.status_code == 200
        body = json.loads(r.text)
        assert set(body.keys()) == {"slug"}
        assert isinstance(body["slug"], str)


def test_flat_mode_works_without_any_auth_token(client: TestClient):
    """Скрипт оценки шлёт фото без Bearer-токена вообще (case.md не
    упоминает авторизацию) — эндпоинт обязан работать анонимно."""
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=True)
    assert r.status_code == 200


# --- v0.4.4 (ревью 04, блокер 1): "несгораемость flat ДО КОНЦА" -------------

class _ExplodingImageIndex:
    index_version = "exploding-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        raise RuntimeError("не ValueError — ровно то, что flat обязан пережить")

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_flat_mode_survives_non_value_error_exception(client: TestClient, app):
    """contracts/image-scan.md v0.4.4: flat ловит ЛЮБОЕ исключение, не только
    ValueError. До этой правки `except ValueError` пропускал бы любой другой
    сбой пайплайна (индекс/БД/что угодно) наружу как 500 — ровно то, чего
    "несгораемость flat" не допускает."""
    app.state.image_index = _ExplodingImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=True)
    assert r.status_code == 200
    assert r.json() == {"slug": ""}


def test_flat_mode_accepts_first_file_field_regardless_of_name(client: TestClient):
    """v0.4.4: скрипт кейсодержателя может назвать multipart-поле не "image"
    — берём первое файловое поле формы независимо от имени."""
    r = client.post(
        "/v1/scan/photo?flat=1",
        files={"photo": ("label.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}


def test_scan_flat_default_env_makes_flat_the_default_without_query_param(client: TestClient, app):
    """v0.4.4: SCAN_FLAT_DEFAULT=1 — страховка на случай, если скрипт
    кейсодержателя вообще не знает про ?flat=1. Без query-параметра
    /scan/photo обязан вести себя как flat."""
    app.state.settings = dataclasses.replace(app.state.settings, scan_flat_default=True)
    r = client.post(
        "/v1/scan/photo",  # без ?flat вообще
        files={"image": ("label.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json() == {"slug": "shato-vymysel-cabernet"}  # ровно flat-форма, не rich


def test_scan_flat_default_env_yields_to_explicit_query_param(client: TestClient, app):
    """Явный ?flat=0 обязан пересилить SCAN_FLAT_DEFAULT=1 — это страховка на
    неизвестный случай скрипта, а не отмена самого query-параметра."""
    app.state.settings = dataclasses.replace(app.state.settings, scan_flat_default=True)
    r = client.post(
        "/v1/scan/photo?flat=0",
        files={"image": ("label.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    assert r.status_code == 200
    assert set(r.json().keys()) == {
        "slug", "card", "confidence", "ocr_verified", "timing_ms",
        "not_in_catalog", "similar", "analogs", "matches", "candidates",
    }  # rich-форма — explicit ?flat=0 пересилил env-дефолт


# --- rich mode: full schema --------------------------------------------------

def test_rich_mode_confident_match_full_schema(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "slug", "card", "confidence", "ocr_verified", "timing_ms",
        "not_in_catalog", "similar", "analogs", "matches", "candidates",
    }
    assert body["slug"] == "shato-vymysel-cabernet"

    # v0.4.3 (пробел нашёл F): matches — top-5 {slug, score} по убыванию, для
    # eval-раннера (F1-top5 иначе вырождается в F1-top1 через живой API). UI
    # это поле не рендерит — тут только проверяем форму и что оно вообще есть.
    assert 1 <= len(body["matches"]) <= 5
    assert set(body["matches"][0].keys()) == {"slug", "score"}
    assert body["matches"][0]["slug"] == "shato-vymysel-cabernet"
    assert body["matches"][0]["score"] == body["confidence"]["top1_score"]
    scores = [m["score"] for m in body["matches"]]
    assert scores == sorted(scores, reverse=True), "matches обязаны идти по убыванию score"
    assert body["card"]["source"]["name"]
    assert body["card"]["derived"]["sensory"]
    assert body["not_in_catalog"] is False
    assert body["ocr_verified"] is False

    # v0.4.1 (пробел нашёл C): card — РОВНО тело GET /wines/{id}, включая
    # similar, не усечённая форма без него.
    assert set(body["card"].keys()) == {"wine_id", "source", "derived", "source_url", "similar"}
    assert body["card"]["wine_id"] == "shato-vymysel-cabernet"
    assert isinstance(body["card"]["similar"], list)

    wines_r = client.get(f"/v1/wines/{body['card']['wine_id']}", headers=auth_header(register_user(
        client, email="photo-card-parity@example.com")))
    assert wines_r.status_code == 200
    assert wines_r.json() == body["card"], "card в /scan/photo обязан буквально совпадать с GET /wines/{id}"

    conf = body["confidence"]
    assert set(conf.keys()) == {"top1_score", "gap", "f1_top1", "f1_top5", "eval_missing"}
    assert conf["top1_score"] > 0.9
    assert conf["eval_missing"] is True  # нет eval-отчёта — датасет кейса не приехал


def test_rich_mode_low_confidence_is_not_in_catalog_with_similar(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["card"] is None
    assert body["similar"]
    assert body["similar"][0]["wine_id"] == "rozovyy-mirazh"
    # v0.4.3: matches — про сырой ANN top-5 для eval, не про итоговое решение
    # "confident" — обязано быть заполнено И на низкой уверенности (иначе
    # F1-top5 именно на трудных случаях остался бы неизмерим через живой API).
    assert body["matches"]
    assert body["matches"][0]["slug"] == "rozovyy-mirazh"


def test_rich_mode_unrecognized_photo_is_honest_not_found(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:unknown", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["similar"] == []
    assert body["analogs"] == []
    assert body["matches"] == []  # v0.4.3: ANN ничего не нашёл — top-5 честно пуст


def test_rich_mode_empty_file_is_honest_400(client: TestClient):
    r = _photo(client, b"", flat=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_rich_mode_oversized_file_is_honest_400(client: TestClient):
    # v0.4.4: лимит поднят до 25 МБ (было 8) — филлер должен реально его
    # превышать, иначе тест перестал бы проверять лимит вообще.
    r = _photo(client, b"0" * (26 * 1024 * 1024), flat=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_rich_mode_accepts_photo_up_to_new_25mb_limit(client: TestClient):
    """v0.4.4 (ревью 04, блокер 1): 8 МБ -> 25 МБ — телефонные фото часто
    больше 8 МБ. Байты за MOCKPHOTO-конвенцией + честный филлер до ~10 МБ:
    раньше это гарантированно резалось лимитом, теперь обязано пройти как
    обычный (пусть и не распознанный по хвосту) файл."""
    payload = b"MOCKPHOTO:shato-vymysel-cabernet" + b"\x00" * (10 * 1024 * 1024)
    assert len(payload) > 8 * 1024 * 1024
    r = _photo(client, payload, flat=False)
    assert r.status_code == 200


def test_rich_mode_accepts_first_file_field_regardless_of_name(client: TestClient):
    """v0.4.4: та же подстраховка, что и у flat — см.
    test_flat_mode_accepts_first_file_field_regardless_of_name."""
    r = client.post(
        "/v1/scan/photo",
        files={"upload": ("label.jpg", b"MOCKPHOTO:shato-vymysel-cabernet", "image/jpeg")},
    )
    assert r.status_code == 200
    assert r.json()["slug"] == "shato-vymysel-cabernet"


def test_rich_mode_no_file_field_at_all_is_honest_400(client: TestClient):
    """Нет ни "image", ни какого-либо другого файлового поля вообще — честный
    400, не 422 фреймворка и не 500 (image теперь Optional ради п.
    "первое файловое поле независимо от имени")."""
    r = client.post("/v1/scan/photo", data={"not_a_file": "just text"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"


def test_rich_mode_works_for_guest_and_authenticated_user_too(client: TestClient):
    for headers in (auth_header(make_guest(client)), auth_header(register_user(client, email="photo1@example.com"))):
        r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False, headers=headers)
        assert r.status_code == 200


# --- near-dup -> OCR verifier -------------------------------------------------

def test_near_dup_top_candidates_trigger_ocr_verification(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:near-dup", flat=False)
    body = r.json()
    assert body["ocr_verified"] is True
    assert body["slug"] in {"mock-tainoe-vino-2022", "mock-tainoe-vino-2023"}


def test_near_dup_ocr_can_override_top_ann_choice(client: TestClient):
    """OCR "читает" 2022 явно — итоговый slug должен быть 2022, даже если
    top-1 ANN был 2023 (или наоборот) — ровно то, зачем верификатор нужен."""
    r = _photo(client, b"MOCKPHOTO:near-dup:mock-tainoe-vino-2022", flat=False)
    assert r.json()["slug"] == "mock-tainoe-vino-2022"
    assert r.json()["ocr_verified"] is True


def test_near_dup_ocr_failure_is_honestly_not_in_catalog(client: TestClient):
    """v0.4.5: до этой волны OCR-неудача честно откатывалась на top-1 ANN
    ("мы не смогли прочитать этикетку, но что-то похожее нашли"). Ревью 04 /
    калибровка F2 поменяли семантику: маленький gap (near-dup-диапазон) БЕЗ
    успешной OCR-верификации теперь и есть определение недостаточной маржи
    (CV_MARGIN_FLOOR) — та же двусмысленность, ради которой вообще звали
    OCR, честно уезжает в not_in_catalog, а не превращается в уверенный
    (но потенциально неверный) выбор одного из members пары."""
    r = _photo(client, b"MOCKPHOTO:near-dup:NONE", flat=False)
    body = r.json()
    assert body["ocr_verified"] is False
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    # flat, тем не менее, обязан продолжать отдавать лучший ANN-угад — см.
    # test_flat_mode_near_dup_still_returns_ocr_resolved_slug и
    # test_flat_mode_always_returns_best_slug_even_at_low_confidence: not_in_catalog
    # — честность rich/UI, а не сигнал "нечего вернуть" для скрипта оценки.


def test_flat_mode_near_dup_still_returns_ocr_resolved_slug(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:near-dup:mock-tainoe-vino-2022", flat=True)
    assert r.json() == {"slug": "mock-tainoe-vino-2022"}


# --- v0.4.5 (ревью 04, калибровка F2): not_in_catalog по марже, не по score --

class _LowScoreHighGapImageIndex:
    """Изолирует ветвь CV_ABS_FLOOR правила: маржа (gap) щедрая, единственная
    причина not_in_catalog — низкий score сам по себе. Зеркальный случай
    (высокий score, низкая маржа, OCR не спас) — уже
    test_near_dup_ocr_failure_is_honestly_not_in_catalog выше."""

    index_version = "low-score-high-gap-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        # score=0.7 < CV_ABS_FLOOR(0.82, v0.4.9), gap=0.5 >= CV_MARGIN_FLOOR
        # (0.02, v0.4.9) — маржа никаких сомнений не сигналит, дело чисто в
        # слабом score.
        return [Match(slug="shato-vymysel-cabernet", score=0.7, gap=0.5, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_not_in_catalog_triggers_on_low_score_alone_even_with_wide_margin(client: TestClient, app):
    """v0.4.5: правило — OR, не AND. Достаточно провалить ХОТЯ БЫ один из
    двух порогов (contracts/image-scan.md: "top1_score < CV_ABS_FLOOR ИЛИ
    ... gap < CV_MARGIN_FLOOR"). Здесь маржа щедрая (0.5) — ветвь по gap
    сама по себе результат бы не изменила; not_in_catalog обязан всё равно
    сработать чисто по низкому score."""
    app.state.image_index = _LowScoreHighGapImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["confidence"]["top1_score"] == 0.7
    assert body["confidence"]["gap"] == 0.5


# --- v0.4.9 (контракт, свип оркестратора): новые стартовые точки порогов ----

def test_default_settings_pin_v049_threshold_starting_points():
    """Пин значений по умолчанию — источник reports/b5-gate-v048.md (свип
    оркестратора: 1982 синт-позитива с family-gap живым + 45 импосторов + 7
    полевых полок), НЕ калибровка этой правки. Если этот тест упал —
    кто-то откатил дефолты config.py, не поменяв заодно контракт/отчёт."""
    from app.config import Settings
    settings = Settings()
    assert settings.cv_abs_floor == 0.82
    assert settings.cv_margin_floor == 0.02


class _BetweenOldAndNewFloorsImageIndex:
    """score=0.85 — между старым (0.9) и новым (0.82) CV_ABS_FLOOR: под
    v0.4.5/v0.4.7 это был бы not_in_catalog по полу, под v0.4.9 — confident.
    Показывает, что дефолты реально ПРИМЕНЯЮТСЯ (не только задекларированы),
    а не просто "тест того же качественного направления, что и раньше"."""

    index_version = "between-old-new-floors-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [Match(slug="shato-vymysel-cabernet", score=0.85, gap=0.5, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_v049_abs_floor_actually_lowered_not_just_documented(client: TestClient, app):
    app.state.image_index = _BetweenOldAndNewFloorsImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False, "0.85 >= новый CV_ABS_FLOOR(0.82) — обязан быть confident"
    assert body["slug"] == "shato-vymysel-cabernet"


class _BetweenOldAndNewMarginsImageIndex:
    """gap=0.05 — между новым (0.02) и старым (0.25) CV_MARGIN_FLOOR: под
    v0.4.5-v0.4.8 это not_in_catalog по марже, под v0.4.9 — margin пройдена
    (score сам по себе выше пола)."""

    index_version = "between-old-new-margins-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [Match(slug="shato-vymysel-cabernet", score=0.95, gap=0.05, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_v049_margin_floor_actually_lowered_not_just_documented(client: TestClient, app):
    app.state.image_index = _BetweenOldAndNewMarginsImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False, "gap=0.05 >= новый CV_MARGIN_FLOOR(0.02) — обязан быть confident"
    assert body["slug"] == "shato-vymysel-cabernet"


# --- v0.4.7 (контракт §2/3, TODO-0 ревью 05): null-gap = доминирование ------
#
# Контекст (reports/f3-synthetic-baseline.md, b4f9608): на боевом каталоге
# case-20260917 `gap` почти всегда null (epsilon-группировка на плотном
# каталоге не находит границы группы вовсе), и полномасштабный baseline
# намерил официальный гейтованный match-rate 6,8% при raw top-1 69,4% —
# ревью 05 завело TODO-0 именно на пересмотр семантики null. Ни один
# существующий тест этого файла до этой волны не констролировал top.gap=None
# у top-1 напрямую (near-dup фикстуры мока всегда несут числовой gap) — все
# тесты ниже новые.

class _DominantNullGapImageIndex:
    """score >= CV_ABS_FLOOR(0.82, v0.4.9), gap=None — "в top-K нет кандидата
    вне семьи top-1" (доминирование), а не неопределённость."""

    index_version = "dominant-null-gap-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [Match(slug="shato-vymysel-cabernet", score=0.95, gap=None, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


class _WeakDominantNullGapImageIndex:
    """Зеркало выше: score < CV_ABS_FLOOR(0.82, v0.4.9), gap=None всё равно."""

    index_version = "weak-dominant-null-gap-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [Match(slug="shato-vymysel-cabernet", score=0.5, gap=None, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


class _SpyLabelVerifier:
    def __init__(self, answer: str | None = None):
        self.calls: list[list[dict]] = []
        self._answer = answer

    def verify(self, image_bytes: bytes, candidates: list[dict]) -> str | None:
        self.calls.append(candidates)
        return self._answer


def test_null_gap_with_high_score_is_confident_not_margin_failure(client: TestClient, app):
    """v0.4.7 §2 (TODO-0, главное): `gap is None` — доминирование, маржа
    считается пройденной; единственный путь not_in_catalog на null-gap —
    провал CV_ABS_FLOOR (проверяется отдельно ниже). Один-единственный матч —
    заодно пин на то, что верификатор НЕ зовётся зря (нечего различать)."""
    app.state.image_index = _DominantNullGapImageIndex()
    spy = _SpyLabelVerifier()
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == "shato-vymysel-cabernet"
    assert body["confidence"]["gap"] is None
    assert body["confidence"]["top1_score"] == 0.95
    assert spy.calls == [], "единственный кандидат — различать нечего, верификатор не должен звать"


def test_null_gap_with_score_below_floor_is_still_not_in_catalog(client: TestClient, app):
    """Null-gap НЕ спасает от низкого score — единственный легитимный путь
    not_in_catalog при gap=None (v0.4.7 §2: "только по абсолютному полу")."""
    app.state.image_index = _WeakDominantNullGapImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["confidence"]["gap"] is None
    assert body["confidence"]["top1_score"] == 0.5


class _MultiSlugNullGapImageIndex:
    """Три РАЗЛИЧНЫХ слага в top-K, top1.gap=None. По v0.4.7 §1 это означает
    "в top-K нет кандидата вне семьи top-1" — то есть все три, по построению
    метрики, члены ОДНОЙ семьи: ровно сценарий §3 ("несколько членов одной
    семьи в top-K"), верификатор обязан вызваться."""

    index_version = "multi-slug-null-gap-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [
            Match(slug="family-member-a", score=0.95, gap=None, view="real"),
            Match(slug="family-member-b", score=0.94, gap=None, view="synth-1"),
            Match(slug="family-member-c", score=0.93, gap=None, view="synth-2"),
        ]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_verifier_called_for_family_members_in_top_k_even_when_gap_is_null(client: TestClient, app):
    """contracts/image-scan.md v0.4.7 §3: "если top-K содержит несколько
    членов одной семьи — верификатор вызывается ... независимо от исхода
    маржинальной проверки". До этой волны near-dup routing целиком
    пропускался при gap=None (`if top.gap is not None and ...`) — на
    практике (F3 baseline) это значило "верификатор внутри семьи почти
    никогда не вызывается на боевом каталоге", ровно диагноз review 05 TODO-2
    (Мускатель Массандра). Здесь маржа УЖЕ "пройдена" null-ом (доминирование),
    но верификатор обязан всё равно вызваться и его ответ — примениться.

    v0.4.8: отбор кандидатов теперь по близости SCORE (CV_VERIFY_PROXIMITY),
    не по семье/gap — этот тест продолжает проходить, потому что все три
    "члена семьи" здесь и так в пределах proximity (0.95/0.94/0.93). Тест
    остаётся как регресс на связку "верификатор вызывается, даже когда
    маржа уже 'пройдена'" — само слово "семья" в названии теста теперь
    исторический контекст, не механизм отбора (см. v0.4.8-тесты ниже,
    построенные БЕЗ понятия семьи вообще, ровно как реальный кейс q2)."""
    app.state.image_index = _MultiSlugNullGapImageIndex()
    spy = _SpyLabelVerifier(answer="family-member-b")
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()

    assert spy.calls, "верификатор обязан был вызваться — top-K держит 3 члена одной семьи при gap=None"
    called_slugs = {c["slug"] for c in spy.calls[0]}
    assert called_slugs == {"family-member-a", "family-member-b", "family-member-c"}
    assert all(set(c.keys()) == {"slug", "name", "vintage"} for c in spy.calls[0])

    assert body["ocr_verified"] is True
    assert body["slug"] == "family-member-b"  # OCR переставил с top-1 ANN (family-member-a)
    assert body["not_in_catalog"] is False


def test_verifier_abstention_with_null_gap_still_confident_via_dominance(client: TestClient, app):
    """Отличие от test_near_dup_ocr_failure_is_honestly_not_in_catalog (тот
    сценарий — НЕНУЛЕВОЙ маленький gap, то есть реальный внешний конкурент
    близко, и честная деградация в not_in_catalog при неудаче OCR оправдана
    контрактом v0.4.5). Здесь gap=None — конкурента ВНЕ семьи нет вовсе,
    доминирование не отменяется тем, что OCR не смог различить, КТО именно
    внутри семьи на фото — top-1 ANN остаётся confident-ответом."""
    app.state.image_index = _MultiSlugNullGapImageIndex()
    spy = _SpyLabelVerifier(answer=None)  # честно не смог различить
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()

    assert spy.calls, "верификатор обязан был вызваться, даже если в итоге воздержался"
    assert body["ocr_verified"] is False
    assert body["not_in_catalog"] is False, "null-gap доминирование не отменяется воздержанием OCR"
    assert body["slug"] == "family-member-a"  # top-1 ANN как есть, OCR не подтвердил замену


# --- v0.4.8 (контракт, закрытие TODO-2): кандидаты верификатора — близость
# скоров, НЕ family-based gap ------------------------------------------------
#
# Диагноз q2 ("Мускатель Массандра", reports/g4-family-gap.md, трассировка
# G4 на живом каталоге): линейки ОДНОГО дизайна этикетки, но с разными
# НАЗВАНИЯМИ (Портвейн/Мускат/Мускатель) перепись семей НЕ считает одной
# семьёй — family-based отбор кандидатов (v0.4.2-v0.4.7) на такой связке
# схлопывался до одного top1, хотя весь топ-5 держался в пределах 0.033 по
# score. Стабы ниже НАРОЧНО не несут никакого понятия "семья" вообще (ни
# общих префиксов, ни gap, соответствующего family) — ровно как в реальном
# q2 — и всё равно обязаны корректно отобрать кандидатов ПО SCORE.

class _CloseScoresNoSharedFamilyImageIndex:
    """5 РАЗНЫХ, ничем не связанных слагов — визуально близнецы по score
    (разброс 0.02, комфортно внутри дефолтного CV_VERIFY_PROXIMITY=0.04,
    v0.4.9-доп).
    `gap` выставлен произвольно (не null, не по семье ни одной пары) —
    подчёркивает, что gap в этом отборе не читается вовсе."""

    index_version = "close-scores-no-family-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [
            Match(slug="massandra-portveyn-belyy-gurzuf", score=0.930, gap=0.010, view="real"),
            Match(slug="massandra-muskat-belyy-yuzhnoberezhnyy", score=0.923, gap=0.010, view="real"),
            Match(slug="unrelated-wine-a", score=0.917, gap=0.010, view="real"),
            Match(slug="massandra-muskatel-chernyy", score=0.914, gap=0.010, view="real"),
            Match(slug="massandra-muskatel-belyy", score=0.910, gap=0.010, view="real"),
        ]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_verifier_candidates_selected_by_score_proximity_not_family(client: TestClient, app):
    """v0.4.8: 5 разных слагов без единой формальной семьи, но в пределах
    CV_VERIFY_PROXIMITY — верификатор обязан получить ВСЕ пять (cap top-5),
    не только top1. Это прямое воспроизведение диагноза q2: до этой правки
    такая связка давала candidate_slugs=[top1], верификатор не вызывался."""
    app.state.image_index = _CloseScoresNoSharedFamilyImageIndex()
    spy = _SpyLabelVerifier(answer="massandra-muskatel-belyy")
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()

    assert spy.calls, "верификатор обязан был вызваться — весь топ-5 в пределах CV_VERIFY_PROXIMITY"
    called_slugs = {c["slug"] for c in spy.calls[0]}
    assert called_slugs == {
        "massandra-portveyn-belyy-gurzuf", "massandra-muskat-belyy-yuzhnoberezhnyy",
        "unrelated-wine-a", "massandra-muskatel-chernyy", "massandra-muskatel-belyy",
    }, "кандидаты отобраны по близости SCORE, а не по совпадению семьи (её здесь нет вовсе)"
    assert body["slug"] == "massandra-muskatel-belyy"  # OCR переставил с top-1 ANN (portveyn)
    assert body["ocr_verified"] is True
    assert body["not_in_catalog"] is False


class _DominantTopFarFromRestImageIndex:
    """top1 доминирует с большим отрывом (> CV_VERIFY_PROXIMITY) от ВСЕХ
    остальных — регресс-тест, явно потребованный заданием B5: "одиночный
    уверенный топ без соседей -> верификатор не зовётся" (иначе — лишний
    OCR впустую, тратящий бюджет 700 мс без единого реального кандидата)."""

    index_version = "dominant-top-far-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        return [
            Match(slug="clearly-the-one", score=0.97, gap=0.4, view="real"),
            Match(slug="distant-runner-up-a", score=0.60, gap=None, view="real"),
            Match(slug="distant-runner-up-b", score=0.55, gap=None, view="real"),
        ]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_verifier_not_called_when_dominant_top_has_no_close_neighbors(client: TestClient, app):
    app.state.image_index = _DominantTopFarFromRestImageIndex()
    spy = _SpyLabelVerifier()
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert spy.calls == [], "top1 доминирует далеко за пределами CV_VERIFY_PROXIMITY — верификатор лишний"
    assert body["slug"] == "clearly-the-one"
    assert body["ocr_verified"] is False


def test_verifier_candidate_cap_is_explicitly_top_five(client: TestClient, app):
    """Контракт: "cap — top-5" — явно, не полагаясь на top_k вызывающего кода.
    6 слагов в пределах proximity (гипотетически, если бы run_photo_scan()
    когда-нибудь вызвали с top_k>5) — кандидатов на верификатор не больше 5."""

    class _SixCloseScoresImageIndex:
        index_version = "six-close-scores-stub"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return [
                Match(slug=f"slug-{i}", score=0.95 - i * 0.005, gap=0.01, view="real")
                for i in range(6)
            ]

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _SixCloseScoresImageIndex()
    spy = _SpyLabelVerifier()
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    assert spy.calls, "6 близких по score слагов — верификатор обязан вызваться"
    assert len(spy.calls[0]) == 5, "cap top-5 обязан примениться, даже если кандидатов было 6"


# --- "Дополнения v0.4.9" (после e2e B5, reports/b5-gate-v048.md §2 "Причина
# 3"; отчёт этой волны — reports/b6-case-candidates.md): метаданные
# кандидатов верификатора — из КАТАЛОГА КЕЙСА (case-data/slug_refs.json), не
# из нашего RAG/wines-каталога. Живой q2 показал `matched_on: []` на всех
# кандидатах именно по этой причине — RAG_PROVIDER=mock (и наш каталог
# вообще) не знает кейс-слагов и/или отдаёт латиницу, бесполезную для
# кириллических словарей верификатора. Юнит-тесты самого источника (парсинг
# vintage, кэш по пути, битый/отсутствующий файл) — tests/test_case_catalog.py;
# здесь — интеграционная проверка через живой /v1/scan/photo: кандидаты,
# которых реально передают верификатору, обязаны нести метаданные из ПРАВИЛЬНОГО
# источника при смешанном покрытии (часть слагов в case-data, часть — нет).

def test_verifier_candidates_use_case_catalog_metadata_when_present(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Слаги ниже НАРОЧНО отсутствуют в app/rag/fixtures.py::WINES —
    воспроизводит ровно сценарий рассинхрона q2 (кейс-слаги в нашем каталоге
    отсутствуют вовсе). С фикстурой case-data candidate ловит кириллические
    name/winery/vintage вместо деградации до name=slug (латиница/транслит)."""
    slug_refs = {
        "mapping": {
            "case-massandra-muskatel-belyy": {"name": "Мускатель белый", "winery": "Массандра"},
            "case-massandra-portveyn-belyy": {"name": "Портвейн белый, 2019", "winery": "Массандра"},
        }
    }
    (tmp_path / "slug_refs.json").write_text(json.dumps(slug_refs, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    class _CaseCatalogImageIndex:
        index_version = "case-catalog-metadata-stub"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return [
                Match(slug="case-massandra-portveyn-belyy", score=0.930, gap=0.01, view="real"),
                Match(slug="case-massandra-muskatel-belyy", score=0.910, gap=0.01, view="real"),
                Match(slug="case-unknown-elsewhere", score=0.905, gap=0.01, view="real"),
            ]

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _CaseCatalogImageIndex()
    spy = _SpyLabelVerifier(answer="case-massandra-muskatel-belyy")
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200

    assert spy.calls, "верификатор обязан был вызваться — 3 кандидата в пределах proximity"
    by_slug = {c["slug"]: c for c in spy.calls[0]}
    assert by_slug["case-massandra-muskatel-belyy"] == {
        "slug": "case-massandra-muskatel-belyy", "name": "Массандра Мускатель белый", "vintage": None,
    }
    assert by_slug["case-massandra-portveyn-belyy"] == {
        "slug": "case-massandra-portveyn-belyy", "name": "Массандра Портвейн белый, 2019", "vintage": 2019,
    }
    # слаг, которого нет НИ в case-data, НИ в RAG (mock) — честная деградация, как и раньше.
    assert by_slug["case-unknown-elsewhere"] == {
        "slug": "case-unknown-elsewhere", "name": "case-unknown-elsewhere", "vintage": None,
    }
    assert r.json()["ocr_verified"] is True


def test_verifier_candidates_fall_back_to_get_by_id_without_case_data(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Контракт: "Фолбэк при отсутствии case-data — прежний путь get_by_id".
    `tmp_path` без `slug_refs.json` — гарантированное отсутствие источника
    (не полагаемся на то, что на этой машине case-data вообще нет — она
    ЕСТЬ, см. reports/b6-case-candidates.md). Слаги — из app/rag/fixtures.py,
    так что RAG-мок реально способен отдать кириллические name/vintage
    через прежний путь, ничего не сломано этой правкой."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    class _RagKnownCloseScoresImageIndex:
        index_version = "rag-known-close-scores-stub"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return [
                Match(slug="shato-vymysel-cabernet", score=0.930, gap=0.01, view="real"),
                Match(slug="tihaya-gavan-pinot-noir", score=0.915, gap=0.01, view="real"),
            ]

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _RagKnownCloseScoresImageIndex()
    spy = _SpyLabelVerifier(answer="tihaya-gavan-pinot-noir")
    app.state.label_verifier = spy
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200

    assert spy.calls
    by_slug = {c["slug"]: c for c in spy.calls[0]}
    assert by_slug["shato-vymysel-cabernet"] == {
        "slug": "shato-vymysel-cabernet", "name": "Шато Вымысел Каберне Совиньон", "vintage": 2022,
    }
    assert by_slug["tihaya-gavan-pinot-noir"] == {
        "slug": "tihaya-gavan-pinot-noir", "name": "Тихая Гавань Пино Нуар", "vintage": 2022,
    }


def test_default_cv_verify_proximity_pinned_to_v049_addendum():
    """Пин "Дополнений v0.4.9" (contracts/image-scan.md, после живого e2e B5,
    reports/b5-gate-v048.md §2 "Причина 1"; применено этой волной — см.
    reports/b6-case-candidates.md): CV_VERIFY_PROXIMITY 0.03 -> 0.04 — цель
    q2 промахивалась мимо старого порога на 0,0029. Если этот тест упал —
    кто-то откатил дефолт config.py, не поменяв заодно контракт/отчёт."""
    from app.config import Settings
    settings = Settings()
    assert settings.cv_verify_proximity == 0.04


# --- timing_ms genuinely measured, not hardcoded -----------------------------

class _SlowImageIndex:
    index_version = "slow-stub"

    def embed(self, image: bytes) -> list[float]:
        return [0.0]

    def search(self, image: bytes, top_k: int = 5) -> list[Match]:
        time.sleep(0.05)
        return [Match(slug="shato-vymysel-cabernet", score=0.95, gap=0.2, view="real")]

    def build(self, refs, version) -> None:
        return None

    def add(self, slug, images) -> None:
        return None


def test_timing_ms_reflects_real_elapsed_time_not_hardcoded(client: TestClient, app):
    app.state.image_index = _SlowImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    # sleep(0.05) внутри search() -> минимум ~50 мс, оставляем запас на
    # таймер/шедулинг, но не даём пройти тесту на захардкоженный 0/константу.
    assert r.json()["timing_ms"] >= 40


def test_timing_ms_is_fast_on_default_mock(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert 0 <= r.json()["timing_ms"] < 1000


# --- Хотфикс (оркестратор, полный прогон реального RAG): AnalogsWineItem.
# winery_name теперь Optional — 92/1982 rich-ответов 500-ли pydantic
# ValidationError, когда у вина боевого каталога винодельня не заполнена
# (None, не отсутствующий ключ — mock-RAG этого не воспроизводил, там у всех
# фикстур винодельня заполнена). ------------------------------------------

def test_rich_mode_similar_wine_with_missing_winery_name_does_not_500(client: TestClient, monkeypatch):
    """rozovyy-mirazh — низкая уверенность (MOCKPHOTO:weak:...) кладёт его
    же карточку в `similar` через ровно тот же AnalogsWineItem(**item), что
    и боевой /scan/photo (routers/scan.py). Временно зануляем винодельню
    ПРЯМО в фикстуре (monkeypatch.setitem — откатится сам после теста) —
    воспроизводит боевые данные без похода в реальный каталог."""
    from app.rag.fixtures import WINES_BY_SLUG
    monkeypatch.setitem(WINES_BY_SLUG["rozovyy-mirazh"], "winery_name", None)

    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["similar"], "сценарий низкой уверенности обязан заполнить similar, как и до хотфикса"
    item = next(w for w in body["similar"] if w["wine_id"] == "rozovyy-mirazh")
    assert item["winery_name"] is None, "честный null, не пустая строка-заглушка"


def test_rich_mode_analog_with_none_name_dropped_none_region_kept(client: TestClient, monkeypatch):
    """Продолжение хотфикса winery_name (пара chateau-de-talu на финальном
    прогоне 17.09): у единичных вин каталога пусты name/region_name.
    region_name=None — честный null в ответе; name=None — карточку не
    отрендерить, элемент отбрасывается строителем ДО схемы (routers/scan.py),
    остальной ответ живёт."""
    from app.rag.fixtures import WINES_BY_SLUG
    monkeypatch.setitem(WINES_BY_SLUG["rozovyy-mirazh"], "region_name", None)

    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)
    assert r.status_code == 200, r.text
    item = next(w for w in r.json()["similar"] if w["wine_id"] == "rozovyy-mirazh")
    assert item["region_name"] is None

    monkeypatch.setitem(WINES_BY_SLUG["rozovyy-mirazh"], "name", None)
    r2 = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)
    assert r2.status_code == 200, r2.text
    assert all(w["wine_id"] != "rozovyy-mirazh" for w in r2.json()["similar"]), (
        "безымянный аналог обязан быть отброшен, а не ронять rich в 500"
    )


# --- v0.4.11 (агент B8): candidates — top-5 позиций, обогащённых карточкой ---
# (contracts/image-scan.md, "разбор чата кейса 21.09"; отчёт волны —
# reports/b8-candidates-card.md). В отличие от `matches` (голый {slug, score}
# для eval), это карточка-сводка для UI ("Возможно, это одно из:") — данные
# из НАШЕГО каталога первым делом, иначе из каталога кейса
# (app/rag/case_catalog.py) — та же деградация, что и у `card`/GET /wines/{id}.

def test_candidates_field_enriched_from_our_catalog_for_confident_match(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    body = r.json()
    assert len(body["candidates"]) == 1
    c = body["candidates"][0]
    assert set(c.keys()) == {
        "wine_id", "name", "winery_name", "region_name", "image_url", "source_url", "score",
    }
    assert c["wine_id"] == "shato-vymysel-cabernet"
    assert c["name"] == "Шато Вымысел Каберне Совиньон"
    assert c["winery_name"] == "Шато Вымысел"
    assert c["region_name"] == "Кубань"
    assert c["image_url"] == "https://example.com/mock-catalog/img/shato-vymysel-cabernet.webp"
    assert c["source_url"] == "https://example.com/mock-catalog/wines/shato-vymysel-cabernet"
    assert c["score"] == body["confidence"]["top1_score"] == body["matches"][0]["score"]


def test_candidates_field_present_even_when_not_in_catalog(client: TestClient):
    """Контракт: "Поле есть всегда" — UI решает, когда его показать
    (not_in_catalog=true, "Возможно, это одно из:"), бэкенд не скрывает
    данные заранее (тот же принцип, что и у matches, v0.4.3)."""
    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["candidates"]
    assert body["candidates"][0]["wine_id"] == "rozovyy-mirazh"
    assert body["candidates"][0]["name"] == "Розовый Мираж"


def test_candidates_is_empty_when_ann_finds_nothing(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:unknown", flat=False)
    assert r.json()["candidates"] == []


def test_candidates_align_with_matches_in_order_slug_and_score(client: TestClient, app):
    """`candidates` — тот же top-K, что и `matches`, просто обогащённый:
    1:1 по длине/порядку/score, wine_id совпадает со slug матча."""
    app.state.image_index = _CloseScoresNoSharedFamilyImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    body = r.json()
    assert len(body["candidates"]) == len(body["matches"]) == 5
    assert [c["wine_id"] for c in body["candidates"]] == [m["slug"] for m in body["matches"]]
    assert [c["score"] for c in body["candidates"]] == [m["score"] for m in body["matches"]]


def test_candidates_fall_back_to_case_catalog_when_rag_does_not_know_slug(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Слаг НАРОЧНО отсутствует в app/rag/fixtures.py::WINES — ровно сценарий
    контракта (122/2054 кейс-слагов вне нашего каталога). image_url/
    source_url обязаны следовать конвенции фолбэка (case-thumbs / vino-svoe.ru),
    не нашей mock-витрине."""
    (tmp_path / "case_catalog.json").write_text(json.dumps({
        "mapping": {
            "case-massandra-muskatel-belyy": {
                "name": "Мускатель белый", "winery_name": "Массандра", "region_name": "Крым",
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    class _CaseOnlySlugImageIndex:
        index_version = "case-only-slug-stub"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return [Match(slug="case-massandra-muskatel-belyy", score=0.91, gap=0.3, view="real")]

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _CaseOnlySlugImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["candidates"] == [{
        "wine_id": "case-massandra-muskatel-belyy",
        "name": "Мускатель белый",
        "winery_name": "Массандра",
        "region_name": "Крым",
        "image_url": "/v1/case-thumbs/case-massandra-muskatel-belyy.webp",
        "source_url": "https://vino-svoe.ru/wines/case-massandra-muskatel-belyy",
        "score": 0.91,
    }]


def test_candidates_degrade_honestly_when_slug_unknown_everywhere(
    client: TestClient, app, tmp_path, monkeypatch
):
    """Слаг не резолвится НИ нашим RAG, НИ каталогом кейса (пустой
    CASE_DATA_DIR — не полагаемся на то, что на этой машине его там правда
    нет, см. tests/test_case_catalog.py) — честная деградация, не 500."""
    monkeypatch.setenv("CASE_DATA_DIR", str(tmp_path))

    class _UnknownEverywhereImageIndex:
        index_version = "unknown-everywhere-stub"

        def embed(self, image: bytes) -> list[float]:
            return [0.0]

        def search(self, image: bytes, top_k: int = 5) -> list[Match]:
            return [Match(slug="totally-unknown-slug", score=0.8, gap=0.3, view="real")]

        def build(self, refs, version) -> None:
            return None

        def add(self, slug, images) -> None:
            return None

    app.state.image_index = _UnknownEverywhereImageIndex()
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200, r.text

    assert r.json()["candidates"] == [{
        "wine_id": "totally-unknown-slug",
        "name": "totally-unknown-slug",
        "winery_name": None,
        "region_name": None,
        "image_url": None,
        "source_url": "https://vino-svoe.ru/wines/totally-unknown-slug",
        "score": 0.8,
    }]


def test_candidates_uses_slug_fallback_when_rag_name_is_explicitly_none(client: TestClient, monkeypatch):
    """Хотфикс (эта волна): `.get("name", slug)` дефолтит только на
    ОТСУТСТВУЮЩИЙ ключ, не на явный None (боевой каталог несёт его у
    единичных позиций — тот же класс дыры, что и у AnalogsWineItem.name в
    similar/analogs). `ScanCandidateItem.name` обязателен — без `or slug`
    здесь был бы 500 pydantic ValidationError, не тихая деградация."""
    from app.rag.fixtures import WINES_BY_SLUG
    monkeypatch.setitem(WINES_BY_SLUG["rozovyy-mirazh"], "name", None)

    r = _photo(client, b"MOCKPHOTO:weak:rozovyy-mirazh", flat=False)

    assert r.status_code == 200, r.text
    c = next(c for c in r.json()["candidates"] if c["wine_id"] == "rozovyy-mirazh")
    assert c["name"] == "rozovyy-mirazh"
