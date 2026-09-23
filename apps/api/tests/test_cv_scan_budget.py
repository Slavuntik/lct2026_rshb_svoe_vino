"""app/cv/service.py — бюджет скана (CV_SCAN_BUDGET_S), дедлайн модели
(VISION_LLM_TIMEOUT_S), предохранитель на модель (_ModelBreaker,
VISION_LLM_BREAKER_FAILS/_COOLDOWN_S) и выбор локального/модельного ответа
(CV_FUSION_CHOOSE) — задача тимлида 22.09, страховка лимита 10с приватной
проверки (reports/devops-hack-v13.md: хвост p50/p95/max 4.4/6.6/9.1с на стенде
4 vCPU) с расширением того же дня (решение Вячеслава: "модель и локальный путь
стартуют одновременно; модель не ответила за 6 с — отдаём локальный ответ;
ответила — выбираем лучший; любые сбои пользователь не замечает ни ошибкой, ни
задержкой") и находкой при расширении (near-dup verify() — общий шаг flat/rich,
раньше без таймаута вовсе, см. reports/ml-eng-scan-budget.md).

Юниты напрямую на `_fusion_text_and_vectors()`/`_verify_with_budget()`/
`_ModelBreaker`/`_choose_fusion_result()` — без HTTP, без реального
encoder/OCR/сети (тот же приём, что test_scan_photo_fusion_ml1_merge_text.py);
плюс HTTP-тесты через `/v1/scan/photo` для бюджета verify() и полей
local_slug/model_slug/answers_agree/chosen_answer_side.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass as _dc

from starlette.testclient import TestClient

from app.config import Settings
from app.cv import service as service_module
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m


def _settings(**overrides) -> Settings:
    return dataclasses.replace(Settings(), **overrides)


# --------------------------------------------------------------------------------------
# Settings: дефолты и env override
# --------------------------------------------------------------------------------------


def test_cv_scan_budget_s_default_is_7_5():
    assert Settings().cv_scan_budget_s == 7.5


def test_cv_scan_budget_s_env_override(monkeypatch):
    monkeypatch.setenv("CV_SCAN_BUDGET_S", "3.0")
    assert Settings().cv_scan_budget_s == 3.0


def test_vision_llm_timeout_s_default_is_6_0():
    """Тимлид 22.09, п.5: 6.5 -> 6.0, решение Вячеслава ("модель не ответила
    за 6 с — отдаём локальный ответ")."""
    assert Settings().vision_llm_timeout_s == 6.0


def test_vision_llm_breaker_defaults():
    s = Settings()
    assert s.vision_llm_breaker_fails == 3
    assert s.vision_llm_breaker_cooldown_s == 60.0


def test_vision_llm_breaker_env_overrides(monkeypatch):
    monkeypatch.setenv("VISION_LLM_BREAKER_FAILS", "5")
    monkeypatch.setenv("VISION_LLM_BREAKER_COOLDOWN_S", "12.5")
    s = Settings()
    assert s.vision_llm_breaker_fails == 5
    assert s.vision_llm_breaker_cooldown_s == 12.5


def test_cv_fusion_choose_default_is_merge():
    assert Settings().cv_fusion_choose == "merge"


def test_cv_fusion_choose_env_override_is_lowercased(monkeypatch):
    monkeypatch.setenv("CV_FUSION_CHOOSE", "MAX_SCORE")
    assert Settings().cv_fusion_choose == "max_score"


# --------------------------------------------------------------------------------------
# _fusion_text_and_vectors(): бюджет OCR — (1) страховка лимита, (6) без 500
# --------------------------------------------------------------------------------------


class _FakeFusionIndex:
    def embed_fusion_query(self, image_bytes: bytes):
        return [1.0], [0.0]


class _SlowVerifier:
    """read_query_text() спит дольше бюджета — симулирует зависший OCR под
    конкуренцией за CPU (тимлид 22.09, разбор devops-hack-v13.md)."""

    def __init__(self, delay_s: float, text: str = "МЕДЛЕННЫЙ ТЕКСТ"):
        self._delay_s = delay_s
        self._text = text
        self.calls = 0

    def read_query_text(self, image_bytes: bytes) -> str:
        self.calls += 1
        time.sleep(self._delay_s)
        return self._text


class _RaisingVerifier:
    def read_query_text(self, image_bytes: bytes) -> str:
        raise RuntimeError("OCR-движок упал")


def test_slow_ocr_is_abandoned_within_budget_and_returns_cv_only():
    """(1)/(3) Бюджет соблюдается при «медленном» фейковом OCR: скан
    возвращается ≤ бюджет+допуск, ответ = только CV (модель не настроена)."""
    settings = _settings(cv_scan_budget_s=0.3, cv_fusion_text_source="ocr")
    verifier = _SlowVerifier(delay_s=2.0)
    t0 = time.monotonic()
    label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndex(), verifier, settings, t0,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.3, f"не должны ждать OCR дольше бюджета+допуска, elapsed={elapsed}"
    assert (label_text, source, ocr_text) == ("", "ocr", "")


def test_slow_ocr_with_model_answered_returns_model_text_not_blocked(monkeypatch):
    """Не успел OCR, модель ответила -> ответ = текст модели (CV+текст модели),
    ожидание OCR не блокирует уже готовый ответ модели."""
    settings = _settings(
        cv_scan_budget_s=0.3, cv_fusion_text_source="vlm",
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
        vision_llm_timeout_s=2.0,  # модель сама успевает — тестируем именно бюджет OCR
    )
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "ТЕКСТ МОДЕЛИ")
    verifier = _SlowVerifier(delay_s=2.0)
    t0 = time.monotonic()
    label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndex(), verifier, settings, t0,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.3, f"elapsed={elapsed}"
    assert label_text == "ТЕКСТ МОДЕЛИ"
    assert source == "vlm"
    assert ocr_text == ""  # OCR не успел — честно пусто, не блокирует ответ


def test_ocr_exception_does_not_propagate_and_logs_warning_without_content(caplog):
    """(6) Исключение OCR (f_ocr.result) ловим: ответ из CV, WARNING без
    содержимого фото/текста, никакого исключения наружу (=> никакого 500 в
    роутере)."""
    settings = _settings(cv_scan_budget_s=1.0, cv_fusion_text_source="ocr")
    with caplog.at_level(logging.WARNING, logger="app.cv.service"):
        label_text, source, ocr_text, _vectors = service_module._fusion_text_and_vectors(
            b"photo", _FakeFusionIndex(), _RaisingVerifier(), settings, time.monotonic(),
        )
    assert (label_text, source, ocr_text) == ("", "ocr", "")
    messages = [r.getMessage() for r in caplog.records]
    assert any("OCR" in m for m in messages)
    assert not any("МЕДЛЕННЫЙ" in m or "photo" in m for m in messages), "лог без содержимого"


# --------------------------------------------------------------------------------------
# _verify_with_budget(): near-dup verify() тоже под бюджетом (находка при
# расширении брифа — rich-фото 41.8_22-08-2026_20-56-40.webp 14.6с/3.4с)
# --------------------------------------------------------------------------------------


class _SlowVerifyOnly:
    def __init__(self, delay_s: float, answer: str = "should-not-be-seen"):
        self._delay_s = delay_s
        self._answer = answer
        self.calls = 0

    def verify(self, image_bytes, candidates, ocr_text=None):
        self.calls += 1
        time.sleep(self._delay_s)
        return self._answer


def test_verify_with_budget_abandons_slow_verify_and_returns_none():
    verifier = _SlowVerifyOnly(delay_s=2.0)
    t0 = time.monotonic()
    result = service_module._verify_with_budget(
        verifier, b"photo", [{"slug": "a", "name": "A", "vintage": None}], None, t0 + 0.3,
    )
    elapsed = time.monotonic() - t0
    assert result is None
    assert elapsed < 1.3, f"не должны ждать verify() дольше дедлайна+допуска, elapsed={elapsed}"


def test_verify_with_budget_returns_fast_answer_normally():
    class _FastVerify:
        def verify(self, image_bytes, candidates, ocr_text=None):
            return "a"

    result = service_module._verify_with_budget(
        _FastVerify(), b"photo", [{"slug": "a", "name": "A", "vintage": None}], None, time.monotonic() + 5.0,
    )
    assert result == "a"


class _FastOcrSlowVerify:
    """read_query_text() мгновенный (не съедает бюджет ДО verify()), verify()
    спит дольше бюджета — изолирует именно бюджет near-dup verify()."""

    def __init__(self, delay_s: float, ocr_text: str = ""):
        self._delay_s = delay_s
        self._ocr_text = ocr_text
        self.verify_calls = 0

    def read_query_text(self, image_bytes: bytes) -> str:
        return self._ocr_text

    def verify(self, image_bytes, candidates, ocr_text=None):
        self.verify_calls += 1
        time.sleep(self._delay_s)
        return "close-rival"


def test_fusion_verify_budget_exhausted_degrades_to_top1_without_hanging(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """CV_FUSION_VERIFY=1 + медленный verify() -> ответ ≤ бюджет+допуск,
    ocr_verified=False (честная деградация на top-1 слияния, не 500/не зависание)."""
    idx = _FusionImageIndex([_m("ann-top1", 0.90), _m("close-rival", 0.895)])
    app.state.image_index = idx
    app.state.label_verifier = _FastOcrSlowVerify(delay_s=2.0)
    _enable_fusion(app, tmp_path, monkeypatch, cv_fusion_verify=True, cv_scan_budget_s=0.5)

    t0 = time.monotonic()
    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert elapsed < 1.5, f"elapsed={elapsed}"
    assert r.json()["ocr_verified"] is False


def test_non_fusion_near_dup_verify_budget_exhausted_degrades_gracefully(client: TestClient, app):
    """CV_SCAN_BUDGET_S действует и в пути БЕЗ слияния (settings.cv_fusion=False,
    дефолт) — near-dup verify() был без таймаута вовсе в ОБОИХ путях."""
    app.state.settings = dataclasses.replace(app.state.settings, cv_scan_budget_s=0.5)
    app.state.label_verifier = _FastOcrSlowVerify(delay_s=2.0)

    t0 = time.monotonic()
    r = _photo(client, b"MOCKPHOTO:near-dup", flat=False)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert elapsed < 1.5, f"elapsed={elapsed}"
    assert r.json()["ocr_verified"] is False


# --------------------------------------------------------------------------------------
# _ModelBreaker — (8) предохранитель на модель
# --------------------------------------------------------------------------------------


def test_breaker_allows_by_default():
    b = service_module._ModelBreaker()
    assert b.allow(cooldown_s=60) is True


def test_breaker_opens_after_n_consecutive_failures_and_blocks():
    b = service_module._ModelBreaker()
    for _ in range(2):
        b.record_failure(name="vlm", fails_threshold=3, cooldown_s=60)
        assert b.allow(cooldown_s=60) is True, "ещё не набрали порог"
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=60)
    assert b.allow(cooldown_s=60) is False, "3 сбоя/таймаута подряд — открыт"


def test_breaker_success_resets_failure_count():
    b = service_module._ModelBreaker()
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=60)
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=60)
    b.record_success(name="vlm")
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=60)
    assert b.allow(cooldown_s=60) is True, "счётчик сброшен успехом — 1 сбой не открывает"


def test_breaker_half_open_allows_exactly_one_trial_under_concurrency():
    b = service_module._ModelBreaker()
    for _ in range(3):
        b.record_failure(name="vlm", fails_threshold=3, cooldown_s=0.05)
    assert b.allow(cooldown_s=0.05) is False
    time.sleep(0.08)
    assert b.allow(cooldown_s=0.05) is True, "пауза истекла — ровно один пробный скан разрешён"
    assert b.allow(cooldown_s=0.05) is False, "конкурентный вызов сразу после — уже снова 'открыт'"


def test_breaker_trial_success_closes_it():
    b = service_module._ModelBreaker()
    for _ in range(3):
        b.record_failure(name="vlm", fails_threshold=3, cooldown_s=0.05)
    time.sleep(0.08)
    assert b.allow(cooldown_s=0.05) is True
    b.record_success(name="vlm")
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=0.05)
    assert b.allow(cooldown_s=0.05) is True, "предохранитель закрыт — счётчик с нуля"


def test_breaker_trial_failure_reopens_for_new_cooldown():
    b = service_module._ModelBreaker()
    for _ in range(3):
        b.record_failure(name="vlm", fails_threshold=3, cooldown_s=0.05)
    time.sleep(0.08)
    assert b.allow(cooldown_s=0.05) is True  # пробный
    b.record_failure(name="vlm", fails_threshold=3, cooldown_s=0.05)
    assert b.allow(cooldown_s=0.05) is False, "неудачный пробный скан — снова открыт"


def test_fusion_skips_model_after_breaker_opens_without_calling_it(monkeypatch):
    """(8) 3 сбоя/таймаута подряд -> модель пропускается БЕЗ ожидания дедлайна:
    4-й скан не зовёт read_label_or_raise() вовсе."""
    settings = _settings(
        cv_scan_budget_s=5.0, cv_fusion_text_source="vlm",
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k",
        vision_llm_timeout_s=0.05, vision_llm_breaker_fails=3, vision_llm_breaker_cooldown_s=60,
    )
    calls: list[int] = []

    def always_fails(image_bytes, **kw):
        calls.append(1)
        raise service_module.vision_llm.VisionLLMError("boom")

    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", always_fails)
    for _ in range(3):
        service_module._fusion_text_and_vectors(
            b"photo", _FakeFusionIndex(), _SpyLabelVerifier(ocr_text="ocr"), settings, time.monotonic(),
        )
    assert len(calls) == 3

    t0 = time.monotonic()
    service_module._fusion_text_and_vectors(
        b"photo", _FakeFusionIndex(), _SpyLabelVerifier(ocr_text="ocr"), settings, t0,
    )
    assert len(calls) == 3, "4-й скан не должен был звать модель — предохранитель открыт"
    assert time.monotonic() - t0 < 0.5, "пропуск модели не должен ждать дедлайн"


def test_fusion_model_success_after_failures_does_not_open_breaker(monkeypatch):
    """Успешный ответ модели сбрасывает счётчик — 2 сбоя + 1 успех + 2 сбоя НЕ
    открывают предохранитель (не 3 подряд)."""
    settings = _settings(
        cv_scan_budget_s=5.0, cv_fusion_text_source="vlm",
        vision_llm_url="http://fake-gateway.invalid", vision_llm_key="k", vision_llm_timeout_s=0.05,
    )
    outcomes = iter(["fail", "fail", "ok", "fail", "fail"])

    def flaky(image_bytes, **kw):
        outcome = next(outcomes)
        if outcome == "fail":
            raise service_module.vision_llm.VisionLLMError("boom")
        return "ТЕКСТ"

    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", flaky)
    for _ in range(5):
        service_module._fusion_text_and_vectors(
            b"photo", _FakeFusionIndex(), _SpyLabelVerifier(ocr_text="ocr"), settings, time.monotonic(),
        )
    assert service_module._MODEL_BREAKERS["vlm"].allow(cooldown_s=60) is True


# --------------------------------------------------------------------------------------
# _choose_fusion_result() — (9) CV_FUSION_CHOOSE
# --------------------------------------------------------------------------------------


@_dc
class _FakeCandidate:
    slug: str
    final_score: float
    cv_score: float


@_dc
class _FakeFusionResult:
    ranked: list
    confident: bool = False


def test_choose_merge_always_picks_model_regardless_of_local():
    model = _FakeFusionResult(ranked=[_FakeCandidate("model-slug", 0.80, 0.80)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("local-slug", 0.95, 0.95)])
    result, side = service_module._choose_fusion_result(model, local, "merge")
    assert (result, side) == (model, "model")


def test_choose_max_score_picks_higher_final_score():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.80, 0.80)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.90, 0.70)])
    result, side = service_module._choose_fusion_result(model, local, "max_score")
    assert side == "local" and result is local


def test_choose_max_score_keeps_model_when_it_scores_higher():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.90, 0.80)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.80, 0.70)])
    result, side = service_module._choose_fusion_result(model, local, "max_score")
    assert side == "model" and result is model


def test_choose_agree_else_llm_prefers_model_on_disagreement():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.80, 0.80)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.90, 0.90)])
    result, side = service_module._choose_fusion_result(model, local, "agree_else_llm")
    assert side == "model" and result is model


def test_choose_agree_else_cv_prefers_higher_cv_score_on_disagreement():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.80, 0.70)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.75, 0.90)])
    result, side = service_module._choose_fusion_result(model, local, "agree_else_cv")
    assert side == "local" and result is local


def test_choose_agree_else_modes_return_model_when_slugs_match():
    model = _FakeFusionResult(ranked=[_FakeCandidate("same", 0.80, 0.70)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("same", 0.75, 0.90)])
    for mode in ("agree_else_llm", "agree_else_cv"):
        result, side = service_module._choose_fusion_result(model, local, mode)
        assert (result, side) == (model, "model"), mode


def test_choose_empty_local_ranked_falls_back_to_model():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.8, 0.8)])
    local = _FakeFusionResult(ranked=[])
    result, side = service_module._choose_fusion_result(model, local, "max_score")
    assert (result, side) == (model, "model")


def test_choose_empty_model_ranked_falls_back_to_local():
    model = _FakeFusionResult(ranked=[])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.8, 0.8)])
    result, side = service_module._choose_fusion_result(model, local, "max_score")
    assert (result, side) == (local, "local")


def test_choose_unknown_mode_falls_back_to_model():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.8, 0.8)])
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.95, 0.95)])
    result, side = service_module._choose_fusion_result(model, local, "bogus-mode")
    assert (result, side) == (model, "model")


def test_choose_confident_else_cv_uses_model_when_model_result_confident():
    """ml-lead (reports/ml-lead-choose-rule.md, qa/real_photos_choose_rule.py) —
    доверяем модели, ТОЛЬКО если её fuse() сама прошла гейт уверенности."""
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.80, 0.70)], confident=True)
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.95, 0.95)], confident=True)
    result, side = service_module._choose_fusion_result(model, local, "confident_else_cv")
    assert (result, side) == (model, "model")


def test_choose_confident_else_cv_falls_back_to_local_when_model_result_not_confident():
    model = _FakeFusionResult(ranked=[_FakeCandidate("m", 0.80, 0.70)], confident=False)
    local = _FakeFusionResult(ranked=[_FakeCandidate("l", 0.75, 0.90)], confident=True)
    result, side = service_module._choose_fusion_result(model, local, "confident_else_cv")
    assert (result, side) == (local, "local")


def test_choose_confident_else_cv_ignores_agreement_entirely():
    """Независимо от того, совпадают ли слаги — решает ТОЛЬКО model_result.confident,
    не сравнение с local (в отличие от agree_else_*)."""
    model = _FakeFusionResult(ranked=[_FakeCandidate("same", 0.80, 0.70)], confident=False)
    local = _FakeFusionResult(ranked=[_FakeCandidate("same", 0.80, 0.70)], confident=True)
    result, side = service_module._choose_fusion_result(model, local, "confident_else_cv")
    assert (result, side) == (local, "local"), "model.confident=False -> локальный, даже при совпадении слагов"


# --------------------------------------------------------------------------------------
# HTTP: local_slug/model_slug/answers_agree/chosen_answer_side — служебные поля,
# CV_FUSION_CHOOSE меняет видимый ответ только при значении != "merge"
# --------------------------------------------------------------------------------------

_CHOOSE_CATALOG_ROWS = [
    {"Slug": "cv-favorite", "Название вина": "Простое Вино", "Винодельня": "Простая Винодельня"},
    {"Slug": "needs-ocr", "Название вина": "Простое Вино Резерв", "Винодельня": "Уникальная Винодельня"},
]


def _run_choose_scenario(client, app, tmp_path, monkeypatch, *, choose: str) -> service_module.PhotoScanResult | dict:
    idx = _FusionImageIndex([_m("cv-favorite", 0.80), _m("needs-ocr", 0.79)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="Уникальная Винодельня")
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "НЕИНФОРМАТИВНЫЙ ТЕКСТ ШУМА")
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_CHOOSE_CATALOG_ROWS,
        cv_fusion_w=0.3, cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
        cv_fusion_choose=choose,
    )
    r = _photo(client, b"MOCKPHOTO:whatever", flat=False)
    assert r.status_code == 200, r.text
    return r.json()


def test_merge_default_matches_old_behavior_model_text_decides(client: TestClient, app, tmp_path, monkeypatch):
    body = _run_choose_scenario(client, app, tmp_path, monkeypatch, choose="merge")
    assert body["matches"][0]["slug"] == "cv-favorite", "модель не различила — решает CV, как раньше"


def test_max_score_can_let_local_ocr_answer_win_over_uninformative_model(
    client: TestClient, app, tmp_path, monkeypatch,
):
    """(9) Модель шумит, OCR прямо называет винодельню needs-ocr -> локальный
    ответ обгоняет модельный по final_score -> max_score должен выбрать local."""
    body = _run_choose_scenario(client, app, tmp_path, monkeypatch, choose="max_score")
    assert body["matches"][0]["slug"] == "needs-ocr"


def _run_photo_scan_direct(app, *, image_bytes: bytes = b"MOCKPHOTO:whatever"):
    """Прямой вызов `run_photo_scan()` (не через HTTP) — местные/модельные поля
    сравнения (`local_slug`/`model_slug`/`answers_agree`/`chosen_answer_side`)
    НЕ входят в контракт (`ScanPhotoRichResponse` их не сериализует, см. брифа
    п.9 "не в контракт"), поэтому недоступны через `r.json()`. Переиспользует
    `app.state.retriever`/`app.state.settings`, уже собранные `_enable_fusion()`
    (тот же приём, что HTTP-обвязка, без лишнего дубля настройки RAG-мока)."""
    return service_module.run_photo_scan(
        image_bytes=image_bytes, image_index=app.state.image_index, verifier=app.state.label_verifier,
        retriever=app.state.retriever, settings=app.state.settings,
    )


def test_scan_photo_result_carries_local_model_agreement_fields_when_they_disagree(
    app, tmp_path, monkeypatch,
):
    """(9) local_slug/model_slug/answers_agree/chosen_answer_side — служебные
    поля PhotoScanResult, посчитанные ВСЕГДА (даже под дефолтным "merge"), для
    офлайн-сравнения ml-lead."""
    idx = _FusionImageIndex([_m("cv-favorite", 0.80), _m("needs-ocr", 0.79)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="Уникальная Винодельня")
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "НЕИНФОРМАТИВНЫЙ ТЕКСТ ШУМА")
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_CHOOSE_CATALOG_ROWS,
        cv_fusion_w=0.3, cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
    )

    result = _run_photo_scan_direct(app)
    assert result.model_slug == "cv-favorite"
    assert result.local_slug == "needs-ocr"
    assert result.answers_agree is False
    assert result.chosen_answer_side == "model"  # дефолт CV_FUSION_CHOOSE=merge
    # "merge" — ответ бит-в-бит как раньше: text_source/label_text — модельные
    assert result.text_source == "vlm_local"


def test_scan_photo_result_agreement_fields_when_both_sides_agree(app, tmp_path, monkeypatch):
    idx = _FusionImageIndex([_m("shato-vymysel-cabernet", 0.90), _m("far-rival", 0.30)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="")
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "")
    _enable_fusion(app, tmp_path, monkeypatch)

    result = _run_photo_scan_direct(app)
    assert result.local_slug == result.model_slug == "shato-vymysel-cabernet"
    assert result.answers_agree is True


def test_chosen_answer_side_local_uses_ocr_as_text_source(client: TestClient, app, tmp_path, monkeypatch):
    """При выборе "local" text_source/label_text честно отражают "ocr"/OCR-текст
    (не модельные значения, которые на самом деле не повлияли на ответ)."""
    idx = _FusionImageIndex([_m("cv-favorite", 0.80), _m("needs-ocr", 0.79)])
    app.state.image_index = idx
    app.state.label_verifier = _SpyLabelVerifier(ocr_text="Уникальная Винодельня")
    monkeypatch.setattr(service_module.vision_llm, "read_label_or_raise", lambda *a, **kw: "НЕИНФОРМАТИВНЫЙ ТЕКСТ ШУМА")
    _enable_fusion(
        app, tmp_path, monkeypatch, catalog_rows=_CHOOSE_CATALOG_ROWS,
        cv_fusion_w=0.3, cv_fusion_text_source="vlm_local", vision_llm_local_url="http://fake-local.invalid",
        cv_fusion_choose="max_score",
    )

    result = _run_photo_scan_direct(app)
    assert result.chosen_answer_side == "local"
    assert result.text_source == "ocr"
    assert result.label_text == "Уникальная Винодельня"
