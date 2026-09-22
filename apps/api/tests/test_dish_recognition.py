"""app/dish_recognition.py — распознавание блюда по фото (contracts/
post-scan.md v1.1 по брифу тимлида 22.09, "Что подать" по фото блюда). Сеть
не трогается: `vision_llm.ask_json_or_raise` и текстовый энкодер
(`_encode_texts`) подменяются, как test_vision_llm.py подменяет
`read_label_or_raise`."""
from __future__ import annotations

import time

import pytest

from app import dish_recognition as dr
from app.config import Settings
from app.cv import vision_llm
from app.cv.service import _MODEL_BREAKERS

_TAGS = [
    "Устрицы", "Сыры", "Блюда из рыбы", "Блюда из птицы", "Салаты",
    "Брускетты", "BBQ", "Азиатская кухня", "Выпечка и десерты",
]


@pytest.fixture(autouse=True)
def _clear_zero_shot_cache():
    dr._ZERO_SHOT_TEXT_CACHE.clear()
    yield
    dr._ZERO_SHOT_TEXT_CACHE.clear()


# --------------------------------------------------------------------------
# resolve_category
# --------------------------------------------------------------------------

def test_resolve_category_exact_match_case_insensitive():
    assert dr.resolve_category("сыры", _TAGS) == "Сыры"
    assert dr.resolve_category("BBQ", _TAGS) == "BBQ"


def test_resolve_category_synonym():
    assert dr.resolve_category("морепродукты", _TAGS) == "Блюда из рыбы"
    assert dr.resolve_category("стейк", _TAGS) == "BBQ"
    assert dr.resolve_category("десерт", _TAGS) == "Выпечка и десерты"


def test_resolve_category_fuzzy_typo():
    assert dr.resolve_category("Азиятская кухня", _TAGS) == "Азиатская кухня"


def test_resolve_category_unresolvable_is_none():
    assert dr.resolve_category("квантовая физика", _TAGS) is None
    assert dr.resolve_category("", _TAGS) is None
    assert dr.resolve_category(None, _TAGS) is None


def test_resolve_category_empty_tags_is_none():
    assert dr.resolve_category("Сыры", []) is None


# --------------------------------------------------------------------------
# _interpret_model_json (через прямой вызов — не требует сети/пула)
# --------------------------------------------------------------------------

def test_interpret_wine_bottle_wins_over_is_food():
    data = {"is_food": True, "is_wine_bottle": True, "category": "Сыры"}
    result = dr._interpret_model_json(data, _TAGS, "vlm")
    assert result["status"] == "bottle"
    assert result["dish"]["source"] == "vlm"
    assert result["dish"]["category"] is None


def test_interpret_not_food():
    data = {"is_food": False, "is_wine_bottle": False}
    result = dr._interpret_model_json(data, _TAGS, "vlm_local")
    assert result["status"] == "not_food"
    assert result["dish"] == {
        "name": "", "category": None, "alternatives": [], "ingredients": [], "source": "vlm_local",
    }


def test_interpret_food_with_resolvable_category():
    data = {
        "is_food": True, "is_wine_bottle": False, "dish": "Паста Карбонара",
        "ingredients": ["паста", "бекон", "яйцо", ""], "category": "сыры",
        "alternatives": ["BBQ", "не существует", "Сыры"],
    }
    result = dr._interpret_model_json(data, _TAGS, "vlm")
    assert result["status"] == "food"
    dish = result["dish"]
    assert dish["name"] == "Паста Карбонара"
    assert dish["category"] == "Сыры"
    assert dish["ingredients"] == ["паста", "бекон", "яйцо"]  # пустая строка отфильтрована
    assert dish["alternatives"] == ["BBQ"]  # "не существует" отброшен, "Сыры" не дублирует category
    assert dish["source"] == "vlm"


def test_interpret_food_with_unresolvable_category_is_unsure_with_all_tags():
    data = {"is_food": True, "is_wine_bottle": False, "dish": "нечто экзотическое", "category": "марсианская кухня"}
    result = dr._interpret_model_json(data, _TAGS, "vlm")
    assert result["status"] == "unsure"
    assert result["dish"]["name"] == "нечто экзотическое"
    assert result["dish"]["category"] is None
    assert result["dish"]["alternatives"] == _TAGS


def test_interpret_food_with_empty_category_is_unsure():
    data = {"is_food": True, "is_wine_bottle": False}
    result = dr._interpret_model_json(data, _TAGS, "vlm")
    assert result["status"] == "unsure"
    assert result["dish"]["alternatives"] == _TAGS


# --------------------------------------------------------------------------
# _ask_models — приоритет шлюза, фолбэк на локальную, переиспользование пула/
# предохранителей app.cv.service (общих со сканером).
# --------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    import dataclasses
    base = Settings()
    return dataclasses.replace(base, **overrides)


def test_ask_models_prefers_gateway_when_it_answers_in_time(monkeypatch):
    calls = []

    def fake(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        calls.append(url)
        return {"is_food": True, "category": "Сыры"} if "gw" in url else {"is_food": True, "category": "BBQ"}

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", fake)
    settings = _settings(
        vision_llm_url="https://gw.example/v1", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8091/v1",
    )
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert source == "vlm"
    assert data["category"] == "Сыры"
    assert sorted(calls) == ["http://127.0.0.1:8091/v1", "https://gw.example/v1"]  # оба запрошены параллельно


def test_ask_models_falls_back_to_local_when_gateway_empty(monkeypatch):
    def fake(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        return {} if "gw" in url else {"is_food": True, "category": "BBQ"}

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", fake)
    settings = _settings(
        vision_llm_url="https://gw.example/v1", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8091/v1",
    )
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert source == "vlm_local"
    assert data["category"] == "BBQ"


def test_ask_models_falls_back_to_local_when_gateway_raises(monkeypatch):
    def fake(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        if "gw" in url:
            raise vision_llm.VisionLLMError("boom")
        return {"is_food": True, "category": "Сыры"}

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", fake)
    settings = _settings(
        vision_llm_url="https://gw.example/v1", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8091/v1",
    )
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert source == "vlm_local"


def test_ask_models_without_any_gateway_configured_never_calls_network(monkeypatch):
    monkeypatch.setattr(
        vision_llm, "ask_json_or_raise",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("не должно вызываться")),
    )
    data, source = dr._ask_models(b"photo", _settings(), "prompt")
    assert data == {} and source == "none"


def test_ask_models_both_fail_returns_none_source(monkeypatch):
    def fake(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        raise vision_llm.VisionLLMError("boom")

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", fake)
    settings = _settings(
        vision_llm_url="https://gw.example/v1", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8091/v1",
    )
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert data == {} and source == "none"


def test_ask_models_hanging_gateway_is_abandoned_after_timeout(monkeypatch):
    def fake(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        if "gw" in url:
            time.sleep(3.0)
            return {"is_food": True, "category": "Сыры"}
        return {"is_food": True, "category": "BBQ"}

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", fake)
    settings = _settings(
        vision_llm_url="https://gw.example/v1", vision_llm_key="k",
        vision_llm_local_url="http://127.0.0.1:8091/v1",
        vision_llm_timeout_s=0.2,
    )
    t0 = time.monotonic()
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert time.monotonic() - t0 < 1.5  # не ждём зависший шлюз дольше общего дедлайна
    assert source == "vlm_local"


def test_ask_models_shares_breaker_with_scan_service(monkeypatch):
    """Бриф тимлида: "шлюз лёг — и скан, и блюдо его пропускают" — один и тот
    же `_MODEL_BREAKERS["vlm"]` (app/cv/service.py) открывается сбоями ЭТОГО
    эндпоинта и блокирует ЕГО ЖЕ следующие попытки без похода в сеть."""
    def failing(image_bytes, *, url, key, model, timeout_s, prompt, image_size=1024, max_tokens=300):
        raise vision_llm.VisionLLMError("boom")

    monkeypatch.setattr(vision_llm, "ask_json_or_raise", failing)
    settings = _settings(vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    assert _MODEL_BREAKERS["vlm"].allow(cooldown_s=60) is True

    for _ in range(settings.vision_llm_breaker_fails):
        dr._ask_models(b"photo", settings, "prompt")

    assert _MODEL_BREAKERS["vlm"].allow(cooldown_s=60) is False  # предохранитель открыт

    calls = []
    monkeypatch.setattr(
        vision_llm, "ask_json_or_raise",
        lambda *a, **k: calls.append(1) or {"is_food": True, "category": "Сыры"},
    )
    data, source = dr._ask_models(b"photo", settings, "prompt")
    assert calls == []  # предохранитель открыт — сеть вообще не трогаем
    assert source == "none"


# --------------------------------------------------------------------------
# zero_shot_classify — _encode_texts подменяется (без реальной модели/сети)
# --------------------------------------------------------------------------


class _StubImageIndex:
    def __init__(self, image_vector: list[float]):
        self.encoder = object()  # достаточно ненулевого значения — _encode_texts подменён
        self._vec = image_vector

    def embed(self, image: bytes) -> list[float]:
        return self._vec


def _labels() -> list[str]:
    return [*_TAGS, dr.NOT_FOOD_LABEL, dr.BOTTLE_LABEL]


def _stub_encode_texts(vectors_by_label: dict[str, list[float]]):
    labels = _labels()

    def fake(encoder, texts):
        return [vectors_by_label[label] for label in labels]

    return fake


def test_zero_shot_no_encoder_is_none():
    class _NoEncoderIndex:
        def embed(self, image: bytes) -> list[float]:
            return [1.0, 0.0]

    assert dr.zero_shot_classify(_NoEncoderIndex(), b"photo", _TAGS, margin=0.05) is None


def test_zero_shot_confident_tag(monkeypatch):
    vectors = {label: [0.0, 1.0] for label in _labels()}
    vectors["Сыры"] = [1.0, 0.0]
    monkeypatch.setattr(dr, "_encode_texts", _stub_encode_texts(vectors))
    result = dr.zero_shot_classify(_StubImageIndex([1.0, 0.0]), b"photo", _TAGS, margin=0.5)
    assert result == ("Сыры", "tag")


def test_zero_shot_not_food(monkeypatch):
    vectors = {label: [0.0, 1.0] for label in _labels()}
    vectors[dr.NOT_FOOD_LABEL] = [1.0, 0.0]
    monkeypatch.setattr(dr, "_encode_texts", _stub_encode_texts(vectors))
    result = dr.zero_shot_classify(_StubImageIndex([1.0, 0.0]), b"photo", _TAGS, margin=0.5)
    assert result == (dr.NOT_FOOD_LABEL, "not_food")


def test_zero_shot_bottle(monkeypatch):
    vectors = {label: [0.0, 1.0] for label in _labels()}
    vectors[dr.BOTTLE_LABEL] = [1.0, 0.0]
    monkeypatch.setattr(dr, "_encode_texts", _stub_encode_texts(vectors))
    result = dr.zero_shot_classify(_StubImageIndex([1.0, 0.0]), b"photo", _TAGS, margin=0.5)
    assert result == (dr.BOTTLE_LABEL, "bottle")


def test_zero_shot_below_margin_is_none(monkeypatch):
    """Строго с порогом отрыва (бриф тимлида) — top1/top2 почти равны."""
    vectors = {label: [0.5, 0.5] for label in _labels()}
    vectors["Сыры"] = [0.51, 0.49]
    monkeypatch.setattr(dr, "_encode_texts", _stub_encode_texts(vectors))
    result = dr.zero_shot_classify(_StubImageIndex([1.0, 0.0]), b"photo", _TAGS, margin=0.5)
    assert result is None


def test_zero_shot_unavailable_encoder(monkeypatch):
    monkeypatch.setattr(dr, "_encode_texts", lambda encoder, texts: None)
    assert dr.zero_shot_classify(_StubImageIndex([1.0, 0.0]), b"photo", _TAGS, margin=0.05) is None


def test_zero_shot_caches_text_embeddings_by_model_name(monkeypatch):
    calls = []

    def fake(encoder, texts):
        calls.append(1)
        return [[1.0, 0.0] if label == "Сыры" else [0.0, 1.0] for label in _labels()]

    monkeypatch.setattr(dr, "_encode_texts", fake)

    class _NamedIndex(_StubImageIndex):
        def __init__(self, vec):
            super().__init__(vec)
            self.encoder = type("E", (), {"model_name": "stub-model"})()

    idx = _NamedIndex([1.0, 0.0])
    dr.zero_shot_classify(idx, b"photo1", _TAGS, margin=0.5)
    dr.zero_shot_classify(idx, b"photo2", _TAGS, margin=0.5)
    assert len(calls) == 1  # второй вызов взял текстовые эмбеддинги из кэша


# --------------------------------------------------------------------------
# _encode_texts — реальные torch/numpy (уже установлены в это окружение),
# но ПОДДЕЛЬНЫЕ model/processor (без весов, без сети) — покрывает реальную
# числовую логику (get_image_features-стиль pooler_output, L2-нормировка).
# --------------------------------------------------------------------------


def test_encode_texts_with_tensor_output_and_pooler_output_fallback():
    torch = pytest.importorskip("torch")

    class _TensorModel:
        def get_text_features(self, **kwargs):
            return torch.tensor([[3.0, 4.0], [0.0, 5.0]])  # норма 5 и 5 — проверяем L2-нормировку

    class _PoolerModel:
        def get_text_features(self, **kwargs):
            class _Out:
                pooler_output = torch.tensor([[1.0, 0.0]])
            return _Out()

    class _FakeProcessor:
        def __call__(self, text, **kwargs):
            return {"input_ids": torch.zeros((len(text),), dtype=torch.long)}

    class _Encoder:
        device = "cpu"

        def __init__(self, model):
            self._model = model
            self._processor = _FakeProcessor()
            self._loaded = False

        def _load(self):
            self._loaded = True

    enc = _Encoder(_TensorModel())
    out = dr._encode_texts(enc, ["a", "b"])
    assert enc._loaded is True
    assert out is not None and len(out) == 2
    assert out[0] == pytest.approx([0.6, 0.8])  # (3,4)/5
    assert out[1] == pytest.approx([0.0, 1.0])

    enc2 = _Encoder(_PoolerModel())
    out2 = dr._encode_texts(enc2, ["a"])
    assert out2 is not None and len(out2) == 1
    assert out2[0] == pytest.approx([1.0, 0.0])


def test_encode_texts_without_text_tower_is_none():
    class _VisionOnlyModel:
        pass  # без get_text_features вовсе

    class _Encoder:
        device = "cpu"
        _model = _VisionOnlyModel()
        _processor = object()

        def _load(self):
            pass

    assert dr._encode_texts(_Encoder(), ["a"]) is None


# --------------------------------------------------------------------------
# recognize_dish_photo — вся цепочка (модель -> zero-shot -> unsure)
# --------------------------------------------------------------------------


def test_recognize_dish_photo_uses_model_when_configured(monkeypatch):
    monkeypatch.setattr(
        vision_llm, "ask_json_or_raise",
        lambda image_bytes, **kw: {"is_food": True, "category": "Сыры", "dish": "Сырная тарелка"},
    )
    settings = _settings(vision_llm_url="https://gw.example/v1", vision_llm_key="k")
    result = dr.recognize_dish_photo(b"photo", _StubImageIndex([1.0, 0.0]), settings)
    assert result["status"] == "food"
    assert result["dish"]["category"] == "Сыры"
    assert result["dish"]["source"] == "vlm"


def test_recognize_dish_photo_falls_back_to_zero_shot_when_no_model(monkeypatch):
    vectors = {label: [0.0, 1.0] for label in _labels()}
    vectors["BBQ"] = [1.0, 0.0]
    monkeypatch.setattr(dr, "_encode_texts", _stub_encode_texts(vectors))
    monkeypatch.setattr(
        vision_llm, "ask_json_or_raise",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    settings = _settings()  # ни шлюз, ни локальная не настроены
    result = dr.recognize_dish_photo(b"photo", _StubImageIndex([1.0, 0.0]), settings)
    assert result == {
        "status": "food",
        "dish": {"name": "", "category": "BBQ", "alternatives": [], "ingredients": [], "source": "zero_shot"},
    }


def test_recognize_dish_photo_unsure_when_nothing_available(monkeypatch):
    monkeypatch.setattr(
        vision_llm, "ask_json_or_raise",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    settings = _settings()

    class _NoEncoderIndex:
        def embed(self, image: bytes) -> list[float]:
            return [1.0, 0.0]

    result = dr.recognize_dish_photo(b"photo", _NoEncoderIndex(), settings)
    assert result["status"] == "unsure"
    assert result["dish"]["alternatives"] == _TAGS
    assert result["dish"]["source"] == "none"
