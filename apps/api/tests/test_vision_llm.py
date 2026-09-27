"""app/cv/vision_llm.py — клиент VLM для чтения этикетки и выбор источника текста в
слиянии CV + текст (CV_FUSION_TEXT_SOURCE). Сеть не трогается: urlopen и read_label
подменяются."""
from __future__ import annotations

import dataclasses
import io
import json
import time

import pytest
from PIL import Image
from starlette.testclient import TestClient

from app.cv import vision_llm
from tests.test_scan_photo import _photo, _SpyLabelVerifier
from tests.test_scan_photo_fusion import _enable_fusion, _FusionImageIndex, _m


def _jpeg(w: int = 300, h: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 30, 40)).save(buf, format="JPEG")
    return buf.getvalue()


class _Resp:
    def __init__(self, payload: dict):
        self._data = json.dumps(payload).encode()
        self._sent = False

    def read(self, amt=None):
        # Тимлид 22.09 (расширение брифа scan-budget, п.7): read_label_or_raise()
        # теперь читает тело ЧАНКАМИ (`resp.read(_READ_CHUNK_BYTES)`, см.
        # app/cv/vision_llm.py::_read_response_within_deadline) — не одним
        # безаргументным `resp.read()`, как раньше. Настоящий `http.client.
        # HTTPResponse.read(amt)` отдаёт данные ОДНИМ куском (тело короче amt)
        # и `b""` на EOF при следующем вызове — воспроизводим ровно это.
        if self._sent:
            return b""
        self._sent = True
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


# --------------------------------------------------------------------------------------
# Клиент
# --------------------------------------------------------------------------------------


def test_parse_fields_accepts_fenced_json_and_ignores_extra_keys():
    content = '```json\n{"winery": "ТАБИЯ", "name": "Пино Нуар", "sugar": "полусухое", "vintage": 2025, "x": 1}\n```'
    fields = vision_llm.parse_fields(content)
    assert fields["winery"] == "ТАБИЯ" and fields["vintage"] == "2025"
    assert "x" not in fields
    assert vision_llm.fields_to_text(fields) == "ТАБИЯ Пино Нуар полусухое 2025"


def test_parse_fields_garbage_is_empty():
    assert vision_llm.parse_fields("не JSON вовсе") == {}
    assert vision_llm.parse_fields('["список"]') == {}


def test_read_label_without_url_does_not_touch_network(monkeypatch):
    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert vision_llm.read_label(_jpeg(), url=None, key="k", model="m", timeout_s=1) == ""


def test_read_label_success_sends_center_crop_and_bearer(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout, context):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        seen["timeout"] = timeout
        return _Resp(_chat_payload('{"winery": "DENISOV", "name": "Красная стрелка", "color": "красное"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    text = vision_llm.read_label(_jpeg(), url="https://gw.example/v1/", key="secret", model="qwen", timeout_s=3)
    assert text == "DENISOV Красная стрелка красное"
    assert seen["url"] == "https://gw.example/v1/chat/completions"
    assert seen["auth"] == "Bearer secret"
    assert seen["timeout"] == 3
    parts = seen["body"]["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert seen["body"]["temperature"] == 0


def test_read_label_local_server_without_key_sends_no_auth_header(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout, context):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(_chat_payload('{"winery": "Фанагория"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    assert vision_llm.read_label(_jpeg(), url="http://127.0.0.1:8091/v1", key=None, model="m", timeout_s=1) == "Фанагория"
    assert seen["auth"] is None


def test_read_label_http_error_and_timeout_fall_back_to_empty(monkeypatch):
    def http_error(*a, **k):
        raise vision_llm.urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b""))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", http_error)
    assert vision_llm.read_label(_jpeg(), url="https://gw", key="k", model="m", timeout_s=1) == ""

    def timeout(*a, **k):
        raise TimeoutError("медленно")

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", timeout)
    assert vision_llm.read_label(_jpeg(), url="https://gw", key="k", model="m", timeout_s=1) == ""


def test_read_label_broken_image_is_empty_without_network(monkeypatch):
    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert vision_llm.read_label(b"not an image", url="https://gw", key="k", model="m", timeout_s=1) == ""


# --------------------------------------------------------------------------------------
# read_label_fields_or_raise() — вето "не бутылка" (27.09, reports/ml-eng-not-a-bottle.md):
# поле `bottle_visible` отдельно от текста этикетки (FIELDS его не видит, см. PROMPT).
# read_label_or_raise() — тонкая обёртка, отбрасывающая bottle_visible (текст бит-в-бит).
# --------------------------------------------------------------------------------------


def test_prompt_asks_for_bottle_visible_field():
    """Страховка от случайного отката промпта — поле реально запрашивается."""
    assert "bottle_visible" in vision_llm.PROMPT


def test_read_label_fields_bottle_visible_false_with_empty_fields(monkeypatch):
    def fake_urlopen(req, timeout, context):
        return _Resp(_chat_payload(
            '{"bottle_visible": false, "winery": "", "name": "", "grapes": "", '
            '"color": "", "sugar": "", "vintage": ""}'
        ))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    text, bottle_visible = vision_llm.read_label_fields_or_raise(
        _jpeg(), url="https://gw.example/v1", key="k", model="m", timeout_s=1,
    )
    assert text == ""
    assert bottle_visible is False


def test_read_label_fields_bottle_visible_true_with_real_text(monkeypatch):
    def fake_urlopen(req, timeout, context):
        return _Resp(_chat_payload('{"bottle_visible": true, "winery": "ТАБИЯ", "name": "Резерв"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    text, bottle_visible = vision_llm.read_label_fields_or_raise(
        _jpeg(), url="https://gw.example/v1", key="k", model="m", timeout_s=1,
    )
    assert text == "ТАБИЯ Резерв"
    assert bottle_visible is True


def test_read_label_fields_missing_field_is_none_not_false(monkeypatch):
    """Старый формат ответа (без bottle_visible вовсе, напр. модель другой
    версии) -> None, НЕ False — вызывающий код не должен голосовать "нет
    бутылки" за отсутствие мнения (см. app/cv/service.py::_no_bottle_signal)."""
    def fake_urlopen(req, timeout, context):
        return _Resp(_chat_payload('{"winery": "ТАБИЯ"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    text, bottle_visible = vision_llm.read_label_fields_or_raise(
        _jpeg(), url="https://gw.example/v1", key="k", model="m", timeout_s=1,
    )
    assert text == "ТАБИЯ"
    assert bottle_visible is None


def test_read_label_fields_non_bool_bottle_visible_is_none(monkeypatch):
    """Модель вернула не-булево значение (напр. строку) -> не доверяем, None."""
    def fake_urlopen(req, timeout, context):
        return _Resp(_chat_payload('{"bottle_visible": "да", "winery": "ТАБИЯ"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    _text, bottle_visible = vision_llm.read_label_fields_or_raise(
        _jpeg(), url="https://gw.example/v1", key="k", model="m", timeout_s=1,
    )
    assert bottle_visible is None


def test_read_label_fields_without_url_touches_no_network(monkeypatch):
    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert vision_llm.read_label_fields_or_raise(_jpeg(), url=None, key="k", model="m", timeout_s=1) == ("", None)


def test_read_label_fields_http_error_raises_vision_llm_error(monkeypatch):
    def http_error(*a, **k):
        raise vision_llm.urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b""))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", http_error)
    with pytest.raises(vision_llm.VisionLLMError):
        vision_llm.read_label_fields_or_raise(_jpeg(), url="https://gw", key="k", model="m", timeout_s=1)


def test_read_label_or_raise_is_a_thin_wrapper_discarding_bottle_visible(monkeypatch):
    """read_label_or_raise() — байт-в-байт старое поведение (текст, без флага)."""
    def fake_urlopen(req, timeout, context):
        return _Resp(_chat_payload('{"bottle_visible": false, "winery": "ТАБИЯ"}'))

    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", fake_urlopen)
    text = vision_llm.read_label_or_raise(_jpeg(), url="https://gw.example/v1", key="k", model="m", timeout_s=1)
    assert text == "ТАБИЯ"


def test_settings_repr_never_shows_the_gateway_key(monkeypatch):
    monkeypatch.setenv("VISION_LLM_KEY", "sk-very-secret")
    from app.config import Settings
    assert "sk-very-secret" not in repr(Settings())


# --------------------------------------------------------------------------------------
# Выбор источника текста в слиянии
# --------------------------------------------------------------------------------------

_CATALOG = [
    {"Slug": "tabiya-roze", "Название вина": "Розе", "Винодельня": "Табия", "Категория": "Розовое"},
    {"Slug": "tabiya-pobeda", "Название вина": "Победа", "Винодельня": "Табия", "Категория": "Красное"},
    {"Slug": "other-wine", "Название вина": "Другое", "Винодельня": "Иная винодельня", "Категория": "Белое"},
]


def _setup(app, tmp_path, monkeypatch, *, source, remote=True, local=False, ocr_text=""):
    idx = _FusionImageIndex([_m("other-wine", 0.86), _m("tabiya-roze", 0.85), _m("tabiya-pobeda", 0.85)])
    app.state.image_index = idx
    spy = _SpyLabelVerifier(ocr_text=ocr_text)
    app.state.label_verifier = spy
    overrides = {"cv_fusion_text_source": source, "cv_fusion_w": 0.3}
    if remote:
        overrides |= {"vision_llm_url": "https://gw.example/v1", "vision_llm_key": "k"}
    if local:
        overrides |= {"vision_llm_local_url": "http://127.0.0.1:8091/v1"}
    _enable_fusion(app, tmp_path, monkeypatch, catalog_rows=_CATALOG, **overrides)
    return idx, spy


def _fake_readers(monkeypatch, *, remote_text="", local_text="", delay_s=0.0):
    calls = []

    def fake(image_bytes, *, url, key, model, timeout_s, image_size=1024):
        calls.append(url)
        if delay_s:
            time.sleep(delay_s)
        text = local_text if url.startswith("http://127.0.0.1") else remote_text
        return text, None

    # Тимлид 22.09 (расширение брифа scan-budget, п.8): _fusion_text_and_vectors()
    # зовёт read_label_fields_or_raise() (различает сбой шлюза и честный пустой
    # ответ для предохранителя _ModelBreaker), read_label_or_raise()/read_label() —
    # тонкие обёртки над ней (27.09, вето "не бутылка" — `bottle_visible=None`
    # здесь означает "модель про бутылку не высказалась", тот же нейтральный
    # результат, что и у ответа без этого поля — вето не срабатывает).
    monkeypatch.setattr(vision_llm, "read_label_fields_or_raise", fake)
    return calls


def test_source_vlm_uses_model_text_and_ranks_by_it(client: TestClient, app, tmp_path, monkeypatch):
    idx, spy = _setup(app, tmp_path, monkeypatch, source="vlm", ocr_text="мусор")
    calls = _fake_readers(monkeypatch, remote_text="Табия Победа красное")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.status_code == 200, r.text
    assert r.json() == {"slug": "tabiya-pobeda"}
    assert calls == ["https://gw.example/v1"]
    assert spy.read_query_text_calls == 1  # OCR читается всегда — фолбэк
    assert idx.search_fusion_calls[-1]["vectors"] == ([1.0], [0.0])  # эмбеддинги не пересчитываются


def test_source_vlm_empty_answer_falls_back_to_ocr(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="vlm", ocr_text="ТАБИЯ PO3E")
    _fake_readers(monkeypatch, remote_text="")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.json() == {"slug": "tabiya-roze"}


def test_source_vlm_without_gateway_settings_never_calls_model(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="vlm", remote=False, ocr_text="ТАБИЯ Победа")
    calls = _fake_readers(monkeypatch, remote_text="не должно понадобиться")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.json() == {"slug": "tabiya-pobeda"}
    assert calls == []


def test_source_ocr_default_never_calls_model(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="ocr", ocr_text="Табия Розе")
    calls = _fake_readers(monkeypatch, remote_text="Табия Победа")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.json() == {"slug": "tabiya-roze"}
    assert calls == []


def test_source_both_glues_texts_and_calls_each_model_once(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="vlm_both", local=True, ocr_text="")
    calls = _fake_readers(monkeypatch, remote_text="Табия", local_text="Победа красное")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.json() == {"slug": "tabiya-pobeda"}
    assert sorted(calls) == ["http://127.0.0.1:8091/v1", "https://gw.example/v1"]


def test_source_local_only(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="vlm_local", remote=True, local=True, ocr_text="")
    calls = _fake_readers(monkeypatch, remote_text="Табия Розе", local_text="Табия Победа")
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert r.json() == {"slug": "tabiya-pobeda"}
    assert calls == ["http://127.0.0.1:8091/v1"]


def test_hanging_model_is_abandoned_after_timeout(client: TestClient, app, tmp_path, monkeypatch):
    _setup(app, tmp_path, monkeypatch, source="vlm", ocr_text="ТАБИЯ PO3E")
    app.state.settings = dataclasses.replace(app.state.settings, vision_llm_timeout_s=0.2)
    _fake_readers(monkeypatch, remote_text="Табия Победа", delay_s=3.0)
    t = time.monotonic()
    r = _photo(client, b"MOCKPHOTO:x", flat=True)
    assert time.monotonic() - t < 1.5  # не ждём зависшую модель дольше общего дедлайна
    assert r.json() == {"slug": "tabiya-roze"}  # решил текст OCR
