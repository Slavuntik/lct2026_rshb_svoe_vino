"""Вето "не бутылка" в CV_FUSION (27.09, reports/ml-eng-not-a-bottle.md) — находка
reports/qa-manual-final.md п.3: фото здания винодельни давало уверенную карточку
«Лесная просека · Кубань-Вино» без единой оговорки (репро на живом API, cv_score
0.8577, воспроизведено дважды). По образцу обратного гейта режима «Блюдо»
(is_food/is_wine_bottle, app/dish_recognition.py): когда модель (vlm/vlm_local)
ПРЯМО говорит `bottle_visible=false`, а кадр уже прошёл гейт `confident` с не
слишком высоким CV-скором (см. app/config.py::cv_not_a_bottle_cv_ceiling — 0.88,
p75 верных уверенных совпадений на 62 живых фото каталога), скан честно отдаёт
not_in_catalog вместо уверенной карточки — тем же способом, что и "нет совпадений
вовсе" (best_guess_slug тоже обнуляется, flat отдаёт пустой slug).

Сеть не трогается: `vision_llm.read_label_fields_or_raise` подменяется (тот же
приём, что test_scan_photo_fusion_ml1_merge_text.py/test_cv_scan_budget.py).
"""
from __future__ import annotations

import dataclasses

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.cv import service as service_module
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m

# Слаг из мок-RAG (app/rag/fixtures.py) — card строится по-настоящему, без
# зависимости от catalog_rows/case-data (тот же приём, что test_scan_photo_fusion.py).
_KNOWN_SLUG = "shato-vymysel-cabernet"


def _no_bottle(*a, **kw) -> tuple[str, bool | None]:
    """Модель честно отвечает: бутылки/этикетки на кадре нет, поля пустые
    (ровно формат ответа, см. vision_llm.PROMPT)."""
    return "", False


# --------------------------------------------------------------------------------------
# Settings: дефолты и env override
# --------------------------------------------------------------------------------------


def test_default_settings_veto_on_with_calibrated_ceiling():
    settings = Settings()
    assert settings.cv_not_a_bottle_veto is True
    assert settings.cv_not_a_bottle_cv_ceiling == 0.88


def test_env_overrides_veto_settings(monkeypatch):
    monkeypatch.setenv("CV_NOT_A_BOTTLE_VETO", "0")
    monkeypatch.setenv("CV_NOT_A_BOTTLE_CV_CEILING", "0.95")
    settings = Settings()
    assert settings.cv_not_a_bottle_veto is False
    assert settings.cv_not_a_bottle_cv_ceiling == 0.95


# --------------------------------------------------------------------------------------
# _no_bottle_signal(): агрегация bottle_visible ответивших моделей
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flags, expected",
    [
        ({}, False),  # ни одна модель не настроена/не ответила
        ({"vlm": None}, False),  # ответила, но без мнения (старый формат ответа)
        ({"vlm": True}, False),  # прямо говорит "бутылка есть"
        ({"vlm": False}, True),  # прямо говорит "бутылки нет"
        ({"vlm": False, "vlm_local": False}, True),  # обе согласны
        ({"vlm": False, "vlm_local": True}, False),  # расхождение — осторожность с ценой ошибки
        ({"vlm": False, "vlm_local": None}, True),  # одна воздержалась, другая сказала "нет"
        ({"vlm": None, "vlm_local": None}, False),  # обе воздержались
    ],
)
def test_no_bottle_signal_aggregation(flags, expected):
    assert service_module._no_bottle_signal(flags) is expected


# --------------------------------------------------------------------------------------
# Живой репро (реальные числа, reports/ml-eng-not-a-bottle.md): cv_score=0.8577,
# доминирующий (единственный) кандидат -> gap=None, что и было на боевом стенде.
# --------------------------------------------------------------------------------------


def test_veto_fires_on_confident_no_bottle_below_ceiling(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", _no_bottle)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["slug"] is None
    assert body["card"] is None
    # matches/candidates остаются — контракт не меняется, поле "есть всегда" (v0.4.3/v0.4.11).
    assert body["matches"][0]["slug"] == _KNOWN_SLUG

    flat = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert flat.json() == {"slug": ""}, "flat — честный пустой slug, тот же исход, что 'нет совпадений вовсе'"


def test_veto_does_not_fire_without_the_flag_confident_card_stays_as_before(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """Контроль: БЕЗ вето (модель не настроена вовсе — cv_fusion_text_source="ocr")
    тот же самый скор 0.8577 остаётся confident, ровно как на живом стенде ДО
    фикса (это и есть баг из находки)."""
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_text_source="ocr")

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == _KNOWN_SLUG


def test_veto_does_not_fire_above_ceiling_protects_strong_match(client: TestClient, app, tmp_path, monkeypatch):
    """Осторожность с ценой ошибки (бриф п.2): визуально ОЧЕНЬ уверенное
    совпадение (>= cv_not_a_bottle_cv_ceiling) не переигрывается, даже если
    модель ошиблась и сказала "бутылки нет"."""
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.95)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", _no_bottle)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == _KNOWN_SLUG
    assert body["card"] is not None


def test_veto_does_not_fire_when_model_confirms_bottle(client: TestClient, app, tmp_path, monkeypatch):
    """Регрессия: обычный путь (модель читает этикетку, bottle_visible=true) не
    затронут вето — карточка как раньше."""
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(
        service_module.vision_llm, "read_label_fields_or_raise",
        lambda *a, **kw: ("ШАТО ВЫМЫСЕЛ КАБЕРНЕ", True),
    )
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == _KNOWN_SLUG


def test_veto_does_not_fire_when_gateway_is_down(client: TestClient, app, tmp_path, monkeypatch):
    """Обязательный сценарий брифа (п.4): шлюз лёг (VisionLLMError) -> модель
    НЕ ответила -> никакого вето, поведение прежнее (честная деградация на
    локальный CV+OCR путь) — иначе теряем отказоустойчивость."""
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")

    def gateway_down(*a, **kw):
        raise service_module.vision_llm.VisionLLMError("boom")

    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", gateway_down)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["not_in_catalog"] is False, "шлюз лёг — фолбэк на CV, не вето"
    assert body["slug"] == _KNOWN_SLUG
    assert body["card"] is not None

    flat = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert flat.json() == {"slug": _KNOWN_SLUG}


def test_veto_disabled_by_settings_flag(client: TestClient, app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", _no_bottle)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
        cv_not_a_bottle_veto=False,
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == _KNOWN_SLUG


def test_veto_ignores_disagreement_between_vlm_and_vlm_local(client: TestClient, app, tmp_path, monkeypatch):
    """vlm_both: шлюз говорит "нет бутылки", локальная модель — "есть, читает
    текст" -> расхождение НЕ считается сигналом (см. _no_bottle_signal), вето
    не срабатывает."""
    idx = _FusionImageIndex([_m(_KNOWN_SLUG, 0.8577)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")

    def disagreeing(image_bytes, *, url, key, model, timeout_s, image_size=1024):
        if url.startswith("http://127.0.0.1"):
            return "ШАТО ВЫМЫСЕЛ", True
        return "", False

    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", disagreeing)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm_both",
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8093/v1",
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is False
    assert body["slug"] == _KNOWN_SLUG


def test_veto_precondition_requires_already_confident_case(client: TestClient, app, tmp_path, monkeypatch):
    """Вето применяется ТОЛЬКО когда кадр И БЕЗ него прошёл бы гейт confident —
    честный not_in_catalog по низкому CV (фото моря из находки) не меняется
    вообще: код-путь тот же, до и после этой правки (similar/analogs как раньше)."""
    idx = _FusionImageIndex([_m("weak-candidate", 0.40)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label_fields_or_raise", _no_bottle)
    _enable_fusion(
        app, tmp_path, monkeypatch,
        cv_fusion_text_source="vlm", vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
        cv_fusion_cv_floor=0.80,
    )

    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    body = r.json()
    assert body["not_in_catalog"] is True
    assert body["confidence"]["top1_score"] == 0.40, "тот же 'слабый CV' исход, что и раньше — не вето"

    flat = _photo(client, b"MOCKPHOTO:whatever", flat=True)
    assert flat.json() == {"slug": "weak-candidate"}, "flat всё ещё отдаёт лучший угад — тут вето не участвует"
