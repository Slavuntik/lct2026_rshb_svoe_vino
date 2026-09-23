"""agents/ML-1-*.md (задача 1) — `CV_FUSION_MERGE_MODEL_TEXT`: в режимах
vlm/vlm_local/vlm_both OCR ДОБАВЛЯЕТСЯ к тексту модели(ей) в слиянии, а не служит
фолбэком только когда НИ ОДНА модель не ответила (`app/cv/service.py::
_fusion_text_and_vectors`). Офлайн-замер (reports/ml-lead-plan.md, 62 живых фото):
"vlm"+OCR 96.8% top-1 против 95.2% у одной "vlm".

Юниты напрямую на `_fusion_text_and_vectors()` (докстринг функции перечисляет
ровно эти случаи) — без HTTP, без реального encoder/OCR/сети; плюс один
end-to-end тест через `/v1/scan/photo`, замыкающий проводку Settings -> service.py
-> `cv.text_fusion.fuse()` (переиспользует обвязку test_scan_photo_fusion.py, тот
же приём кросс-импорта, что test_scan_photo_fusion_h1_cpu_path.py).

Прямые вызовы `_fusion_text_and_vectors()` ниже не передают `t0` — необязательный
параметр (тимлид 22.09, расширение брифа scan-budget), дефолт `None` -> дедлайн
считается от входа в функцию, старое поведение этих юнитов не меняется. Подмена
модели теперь целится в `vision_llm.read_label_or_raise()` (`read_label()` —
тонкая обёртка над ней, см. `app/cv/vision_llm.py`, предохранитель `_ModelBreaker`
в `app/cv/service.py` различает через неё сбой шлюза и честный пустой ответ).
"""
from __future__ import annotations

import dataclasses

from starlette.testclient import TestClient

from app.config import Settings
from app.cv import service as service_module
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m


class _FakeFusionIndexForTextVectors:
    def embed_fusion_query(self, image_bytes: bytes):
        return [1.0], [0.0]


class _FakeVerifierForTextVectors:
    def __init__(self, ocr_text: str):
        self._ocr_text = ocr_text

    def read_query_text(self, image_bytes: bytes) -> str:
        return self._ocr_text


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


# --------------------------------------------------------------------------------------
# Settings: дефолт и env override
# --------------------------------------------------------------------------------------


def test_default_cv_fusion_merge_model_text_is_off():
    assert Settings().cv_fusion_merge_model_text is False


def test_cv_fusion_merge_model_text_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("CV_FUSION_MERGE_MODEL_TEXT", "1")
    assert Settings().cv_fusion_merge_model_text is True


# --------------------------------------------------------------------------------------
# _fusion_text_and_vectors(): три случая брифа + "source не зависит от флага"
# --------------------------------------------------------------------------------------


def test_no_model_answers_ocr_is_the_fallback_text_regardless_of_flag():
    """"Пусто-модель-текст → как фолбэк (не меняется)" — ни vlm, ни vlm_local не
    настроены (readers пуст) -> флаг вообще не читается этой веткой."""
    settings = _settings(
        cv_fusion_text_source="vlm", cv_fusion_merge_model_text=True,
        vision_llm_url=None, vision_llm_key=None, vision_llm_local_url=None,
    )
    label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("ОСОБЫЙ ТЕКСТ OCR"), settings,
    )
    assert (label_text, source, ocr_text) == ("ОСОБЫЙ ТЕКСТ OCR", "ocr", "ОСОБЫЙ ТЕКСТ OCR")


def test_model_answered_flag_off_text_is_model_only_as_before(monkeypatch):
    """"Модель ответила + флаг выключен → как сейчас (текст = только модель)"."""
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ МОДЕЛИ")
    settings = _settings(
        cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
        cv_fusion_merge_model_text=False,
    )
    label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("ОСОБЫЙ ТЕКСТ OCR"), settings,
    )
    assert label_text == "ТЕКСТ МОДЕЛИ"  # OCR НЕ примешан — флаг выключен (дефолт)
    assert source == "vlm_local"
    assert ocr_text == "ОСОБЫЙ ТЕКСТ OCR"  # третье поле верификатора — всегда чистый OCR


def test_model_answered_flag_on_text_is_model_plus_ocr_joined_by_space(monkeypatch):
    """"Модель ответила + флаг включён → текст = модель + OCR через пробел"."""
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ МОДЕЛИ")
    settings = _settings(
        cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
        cv_fusion_merge_model_text=True,
    )
    label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("ОСОБЫЙ ТЕКСТ OCR"), settings,
    )
    assert label_text == "ТЕКСТ МОДЕЛИ ОСОБЫЙ ТЕКСТ OCR"
    assert source == "vlm_local"  # source по-прежнему определяется тем, какая модель ответила
    assert ocr_text == "ОСОБЫЙ ТЕКСТ OCR"


def test_text_source_does_not_depend_on_merge_flag(monkeypatch):
    """"text_source не зависит от флага" — один и тот же source что при
    включённом, что при выключенном флаге, для одного и того же набора ответивших моделей."""
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ МОДЕЛИ")
    base = dict(cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid")
    _, source_off, _, _ = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("OCR"),
        _settings(**base, cv_fusion_merge_model_text=False),
    )
    _, source_on, _, _ = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("OCR"),
        _settings(**base, cv_fusion_merge_model_text=True),
    )
    assert source_off == source_on == "vlm_local"


def test_both_models_answered_source_is_vlm_both_with_or_without_merge(monkeypatch):
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ")
    base = dict(
        cv_fusion_text_source="vlm_both",
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="fake-key",
        vision_llm_local_url="http://fake-local.invalid",
    )
    label_text, source, _ocr, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors("OCR-ТЕКСТ"),
        _settings(**base, cv_fusion_merge_model_text=True),
    )
    assert source == "vlm_both"
    assert label_text == "ТЕКСТ ТЕКСТ OCR-ТЕКСТ"  # vlm + vlm_local (в этом порядке) + OCR


def test_empty_ocr_text_with_flag_on_leaves_no_trailing_space(monkeypatch):
    """OCR не прочитал ничего — при включённом флаге текст остаётся РОВНО текстом
    модели, без хвостового пробела от склейки с пустой строкой."""
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ МОДЕЛИ")
    settings = _settings(
        cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
        cv_fusion_merge_model_text=True,
    )
    label_text, _source, _ocr, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndexForTextVectors(), _FakeVerifierForTextVectors(""), settings,
    )
    assert label_text == "ТЕКСТ МОДЕЛИ"


# --------------------------------------------------------------------------------------
# End-to-end /v1/scan/photo: проводка Settings -> service.py -> cv.text_fusion.fuse()
# --------------------------------------------------------------------------------------

_ML1_CATALOG_ROWS = [
    {"Slug": "cv-favorite", "Название вина": "Простое Вино", "Винодельня": "Простая Винодельня"},
    {"Slug": "needs-ocr", "Название вина": "Простое Вино Резерв", "Винодельня": "Уникальная Винодельня"},
]


def test_http_merge_flag_lets_ocr_promote_candidate_over_model_text_favorite(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """Модель отвечает НЕИНФОРМАТИВНЫМ текстом (не различает кандидатов); OCR
    читает различающее слово винодельни "needs-ocr". Без флага в слияние идёт
    только текст модели -> OCR-сигнал теряется, решает более высокий CV
    "cv-favorite"; с флагом OCR добавляется -> текст поднимает "needs-ocr" над
    CV-фаворитом (тот же механизм, что офлайн-замер 95.2%->96.8%, reports/
    ml-lead-plan.md)."""
    idx = _FusionImageIndex([_m("cv-favorite", 0.80), _m("needs-ocr", 0.79)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="Уникальная Винодельня")
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "НЕИНФОРМАТИВНЫЙ ТЕКСТ ШУМА")

    def _scores(*, merge: bool) -> dict[str, float]:
        _enable_fusion(
            app, tmp_path, monkeypatch, catalog_rows=_ML1_CATALOG_ROWS,
            cv_fusion_w=0.3, cv_fusion_text_source="vlm_local", cv_fusion_merge_model_text=merge,
            vision_llm_local_url="http://fake-local.invalid",
        )
        r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
        assert r.status_code == 200, r.text
        return {m["slug"]: m["score"] for m in r.json()["matches"]}

    off = _scores(merge=False)
    assert off["cv-favorite"] > off["needs-ocr"], "без флага OCR не участвует — решает CV"

    on = _scores(merge=True)
    assert on["needs-ocr"] > on["cv-favorite"], "с флагом OCR поднимает верного кандидата над CV-фаворитом"
