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
        "not_in_catalog", "similar", "analogs", "matches",
    }  # rich-форма — explicit ?flat=0 пересилил env-дефолт


# --- rich mode: full schema --------------------------------------------------

def test_rich_mode_confident_match_full_schema(client: TestClient):
    r = _photo(client, b"MOCKPHOTO:shato-vymysel-cabernet", flat=False)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "slug", "card", "confidence", "ocr_verified", "timing_ms",
        "not_in_catalog", "similar", "analogs", "matches",
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
        # score=0.7 < CV_ABS_FLOOR(0.9), gap=0.5 >= CV_MARGIN_FLOOR(0.3) —
        # маржа никаких сомнений не сигналит, дело чисто в слабом score.
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
    """score >= CV_ABS_FLOOR(0.9), gap=None — "в top-K нет кандидата вне
    семьи top-1" (доминирование), а не неопределённость."""

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
    """Зеркало выше: score < CV_ABS_FLOOR(0.9), gap=None всё равно."""

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
    но верификатор обязан всё равно вызваться и его ответ — примениться."""
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
