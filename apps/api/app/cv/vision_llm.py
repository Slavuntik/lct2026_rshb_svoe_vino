"""Чтение этикетки мультимодальной моделью (VLM) через OpenAI-совместимый шлюз.

Источник текста для слияния CV + текст (`CV_FUSION_TEXT_SOURCE=vlm`, app/cv/service.py).
Замер оркестратора 21.09 на 100 размеченных живых фото кейса (62 из каталога): текст,
который модель прочитала с этикетки (винодельня, название, сорт, цвет, сахар, год), в
слиянии с CV base-384 даёт top-1 95.2% против ~71% с текстом PaddleOCR — модель не путает
кириллицу с латинскими двойниками и читает стилизованные шрифты.

Модуль ничего не решает про вино: только превращает фото в строку текста этикетки.
Любой сбой (нет настроек, сеть, таймаут, HTTP-ошибка, не-JSON) — пустая строка, и
вызывающий код падает обратно на PaddleOCR. Ключ шлюза не логируется никогда.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Центральная бутылка (правило проверки: «распознавать центральную, целиком видимую»):
# доли ширины/высоты кадра x0, y0, x1, y1 — те же, что в замере.
CENTER_CROP = (0.15, 0.05, 0.85, 0.98)
FIELDS = ("winery", "name", "grapes", "color", "sugar", "vintage")
# ml-lead/тимлид 27.09 (reports/ml-eng-not-a-bottle.md, находка reports/qa-manual-final.md
# п.3): добавлено ОДНО поле `bottle_visible` — тот же приём, что `is_food`/`is_wine_bottle`
# в app/dish_recognition.py::_prompt() (обратный гейт режима «Блюдо»), только наоборот —
# здесь дефолтное ожидание "это бутылка", а поле ловит явный отказ модели ("bottle_visible":
# false), когда на кадре нет вообще ни бутылки, ни этикетки (здание, пейзаж и т.п.). Поле
# НЕ входит в FIELDS (parse_fields()/fields_to_text() его не видят и не подмешивают "true"/
# "false" в текст этикетки для слияния) — читается ОТДЕЛЬНО, см. read_label_fields_or_raise().
# Проверено вручную на живом шлюзе (qwen3.8-27b) на кропах здания/моря из qa-manual-final и
# на двух настоящих бутылках каталога (95.63.../1.73... — оба читают этикетку как раньше,
# bottle_visible=true) — формулировка не меняет чтение полей у настоящих бутылок.
PROMPT = (
    "На фото может быть винная бутылка. Смотри только на центральную бутылку, если она видна "
    "целиком. Ответь строго одним JSON без пояснений: "
    '{"bottle_visible": true, "winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. '
    "bottle_visible — false, если на фото нет винной бутылки и этикетки вовсе (здание, пейзаж, "
    "человек, еда, другой предмет) — тогда остальные поля пустые. Если бутылка есть, но текст "
    "этикетки не виден — bottle_visible true, поля пустые. Прочитай этикетку. Русские надписи — "
    "кириллицей, латинские — латиницей. Чего не видно — пустая строка."
)
_JSON_RE = re.compile(r"\{.*\}", re.S)
# Тимлид 22.09 (расширение брифа scan-budget, п.7): читаем тело ответа ЧАНКАМИ,
# не одним `resp.read()` — см. _read_response_within_deadline(). РОВНО 1 байт,
# не "разумный" размер вроде 4096/65536 — эмпирически проверено (тест на
# настоящем медленно-трикльном сервере, apps/api/tests/test_cv_scan_budget_
# fake_gateway.py): `http.client.HTTPResponse.read(amt)` КЛАМПИТ `amt` до
# известного `Content-Length` ответа (`if amt > self.length: amt = self.length`,
# CPython http/client.py), а `io.BufferedReader.read(n)` блокируется, пока не
# соберёт РОВНО `n` байт (или EOF) — то есть ЛЮБОЙ `amt >= Content-Length`
# (весь наш JSON — обычно десятки-сотни байт, `max_tokens=120`) вырождается
# ровно в один `resp.read()` целиком, и цикл ниже ни разу не успевает
# проверить дедлайн ДО того, как тело уже полностью собралось. amt=1 не
# подвержен этому клампингу (1 < любого реального Content-Length) — каждый
# вызов возвращается, как только придёт ХОТЯ БЫ один байт, отдавая циклу
# шанс проверить дедлайн. Цена — до нескольких сотен вызовов `.read(1)` на
# маленький ответ вместо одного — незаметно на фоне сетевого времени (тело
# ответа этого клиента всегда мало, см. FIELDS/max_tokens выше).
_READ_CHUNK_BYTES = 1


class LabelText(str):
    """String-compatible OCR text with the original structured VLM fields.

    Existing readers/ranking consume the string; identity checks retain field
    boundaries instead of trying to recover a wine name from concatenated text.
    """
    def __new__(cls, text: str, fields: dict[str, str] | None = None):
        value = super().__new__(cls, text)
        value.fields = dict(fields or {})
        return value


class VisionLLMError(Exception):
    """Сбой запроса к шлюзу (сеть/HTTP/таймаут/формат) — тимлид 22.09 (расширение
    брифа scan-budget, п.8): предохранителю (`app/cv/service.py::_ModelBreaker`)
    нужно отличать "шлюз сломан" от "модель честно не увидела текста" (валидный
    пустой ответ) — `read_label()` ниже специально схлопывает ОБА случая в ""
    ради старых вызывающих кодов (прогрев и т.п.), `read_label_or_raise()`
    сохраняет различие. Сообщение исключения не несёт `key`/`url`/тело ответа."""


def prepare_image(image_bytes: bytes, size: int) -> str:
    """Кроп центра кадра, даунскейл до `size` по длинной стороне, JPEG q90 → base64."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        x0, y0, x1, y1 = CENTER_CROP
        im = im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def prepare_full_frame_image(image_bytes: bytes, size: int) -> str:
    """Как `prepare_image()`, но БЕЗ `CENTER_CROP` — весь кадр целиком, даунскейл
    до `size` по длинной стороне, JPEG q90 → base64. Для распознавания блюда
    по фото (`ask_json_or_raise()` ниже, contracts/post-scan.md v1.1 по брифу
    тимлида 22.09: "модель читает ВЕСЬ кадр, без центрального кропа
    этикетки") — `read_label()`/`prepare_image()` не трогаются этой правкой,
    остаются на своём CENTER_CROP для этикеток."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def parse_fields(content: str) -> dict[str, str]:
    """Поля этикетки из ответа модели (JSON может быть обёрнут в ```json … ```)."""
    m = _JSON_RE.search(content or "")
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data.get(k) or "").strip() for k in FIELDS}


def fields_to_text(fields: dict[str, str]) -> str:
    return " ".join(fields[k] for k in FIELDS if fields.get(k))


def parse_json_object(content: str) -> dict | None:
    """Как `parse_fields()`, но без фильтра по `FIELDS` — сырой `dict` ответа
    модели (JSON может быть обёрнут в ```json … ```), `None`, если распарсить
    не удалось. Для `ask_json_or_raise()` ниже (промпт распознавания блюда
    несёт свой набор ключей, не `FIELDS` этикетки)."""
    m = _JSON_RE.search(content or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_response_within_deadline(resp, deadline: float) -> bytes:
    """Читает тело ответа ЧАНКАМИ, не одним `resp.read()` — тимлид 22.09 (расширение
    брифа scan-budget, п.7): `timeout=` у `urlopen()`/`http.client.HTTPResponse`
    ограничивает КАЖДУЮ операцию с сокетом ПО ОТДЕЛЬНОСТИ (задокументированное
    поведение `socket.settimeout()`), не весь запрос целиком — шлюз, отдающий тело
    МЕДЛЕННЫМИ порциями (ни одна порция сама по себе не провисает дольше
    `timeout_s`), может растянуть один вызов `resp.read()` намного дольше
    дедлайна, пока поток пула остаётся занят. Проверка дедлайна МЕЖДУ чанками
    ограничивает такое растягивание ОДНИМ провисанием сверх дедлайна (пока чанк
    читается), а не суммой множества мелких порций — НЕ герметичное решение
    (сама библиотека `http.client`/`urllib` не даёт честного способа уменьшить
    таймаут сокета УЖЕ открытого соединения без приватных атрибутов), но
    ограничивает худший случай, а не оставляет его неограниченным вовсе."""
    chunks: list[bytes] = []
    while True:
        chunk = resp.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
        if time.monotonic() > deadline:
            raise TimeoutError("vision_llm: тело ответа читается медленнее дедлайна шлюза")
    return b"".join(chunks)


def read_label_fields_or_raise(
    image_bytes: bytes,
    *,
    url: str | None,
    key: str | None,
    model: str,
    timeout_s: float,
    image_size: int = 1024,
) -> tuple[str, bool | None]:
    """`(текст этикетки, bottle_visible)` — ml-lead/тимлид 27.09 (reports/ml-eng-
    not-a-bottle.md, вето скана по находке reports/qa-manual-final.md п.3).
    `bottle_visible` — `True`/`False`, если модель вернула булево поле PROMPT
    (см. его докстринг), `None` — поле отсутствует/не булево (старый формат
    ответа, нечитаемый JSON, отсутствующий `url`) — вызывающий код (`app/cv/
    service.py::_fusion_text_and_vectors`) тогда честно НЕ учитывает эту модель
    в решении о вето (не голосует ни за, ни против), тот же принцип осторожности,
    что и у остальных полей PROMPT.

    Текст (первый элемент) — БИТ В БИТ то же значение, что вернул бы старый
    `read_label_or_raise()` на тот же ответ модели: тот же `FIELDS`-фильтр,
    просто применённый здесь напрямую (`parse_json_object()` + ручной словарь)
    вместо `parse_fields()` — единственная причина держать оба пути в одной
    функции: `bottle_visible` не входит в `FIELDS` и `parse_fields()` его
    отбросил бы вместе с шумом.

    Раскрывает те же исключения/пустые ответы, что и `read_label_or_raise()`
    (см. её докстринг — теперь тонкая обёртка над этой функцией)."""
    if not url:
        return "", None
    try:
        img = prepare_image(image_bytes, image_size)
    except Exception:  # noqa: BLE001 — битые байты: не сбой шлюза, пусть CV/OCR-путь решает сам
        return "", None
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
        ]}],
        "max_tokens": 120,
        "temperature": 0,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ssl.create_default_context()) as resp:
            raw = _read_response_within_deadline(resp, deadline=time.monotonic() + timeout_s)
            payload = json.loads(raw)
        content = payload["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as exc:
        logger.warning("vision_llm: HTTP %s от шлюза — фолбэк на OCR", exc.code)
        raise VisionLLMError(f"HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут/формат: фолбэк на OCR
        logger.warning("vision_llm: %s — фолбэк на OCR", type(exc).__name__)
        raise VisionLLMError(type(exc).__name__) from exc
    data = parse_json_object(content) or {}
    fields = {k: str(data.get(k) or "").strip() for k in FIELDS}
    text = LabelText(fields_to_text(fields), fields)
    raw_bottle_visible = data.get("bottle_visible")
    bottle_visible = raw_bottle_visible if isinstance(raw_bottle_visible, bool) else None
    return text, bottle_visible


def read_label_or_raise(
    image_bytes: bytes,
    *,
    url: str | None,
    key: str | None,
    model: str,
    timeout_s: float,
    image_size: int = 1024,
) -> str:
    """То же, что `read_label()` ниже, но РАЗЛИЧАЕТ сбой шлюза (сеть/HTTP/таймаут/
    формат — бросает `VisionLLMError`) и легальный пустой ответ модели (""
    остаётся "" — модель ответила, просто не увидела текста). Отсутствующий
    `url` — тоже НЕ сбой (конфигурация, не CV_FUSION_TEXT_SOURCE=vlm*) — как и
    раньше, просто "". Нужен предохранителю `app/cv/service.py::_ModelBreaker`
    (тимлид 22.09, расширение брифа scan-budget, п.8): считать пустые-но-честные
    ответы как "сбой" открывало бы предохранитель на серии нечитаемых фото, а не
    на реально сломанном шлюзе. `key` необязателен (локальный сервер модели без
    авторизации). Ключ шлюза не логируется и не несётся в исключении.

    Тонкая обёртка над `read_label_fields_or_raise()` (27.09, вето "не бутылка") —
    отбрасывает `bottle_visible`, текст БИТ В БИТ как раньше (см. её докстринг)."""
    text, _bottle_visible = read_label_fields_or_raise(
        image_bytes, url=url, key=key, model=model, timeout_s=timeout_s, image_size=image_size,
    )
    return text


def read_label(
    image_bytes: bytes,
    *,
    url: str | None,
    key: str | None,
    model: str,
    timeout_s: float,
    image_size: int = 1024,
) -> str:
    """Текст этикетки (значения полей через пробел) или "" при любом сбое/пустом ответе.

    Тонкая обёртка над `read_label_or_raise()` — сохраняет СТАРЫЙ контракт
    (никогда не бросает) байт-в-байт для существующих вызывающих кодов (прогрев
    `app/cv/factory.py`, любой будущий код, которому различие сбой/пустой ответ
    не нужно). `key` необязателен: локальный сервер модели (`python -m
    mlx_vlm.server`) без авторизации."""
    try:
        return read_label_or_raise(image_bytes, url=url, key=key, model=model, timeout_s=timeout_s, image_size=image_size)
    except VisionLLMError:
        return ""


def ask_json_or_raise(
    image_bytes: bytes,
    *,
    url: str | None,
    key: str | None,
    model: str,
    timeout_s: float,
    prompt: str,
    image_size: int = 1024,
    max_tokens: int = 300,
) -> dict:
    """Общий вызов шлюза за строгим JSON-ответом по ВСЕМУ кадру — НЕ про
    этикетку (contracts/post-scan.md v1.1 по брифу тимлида 22.09,
    "Что подать" по фото блюда): `read_label()`/`read_label_or_raise()` выше
    не используют эту функцию и не меняются ею ни на строку — отдельный путь
    со своим промптом и `prepare_full_frame_image()` (без `CENTER_CROP`).

    Та же дисциплина, что `read_label_or_raise()`:
      - `url` пуст -> `{}` (не сбой — источник просто не настроен, как и там);
      - сеть/HTTP/таймаут -> `VisionLLMError` (сбой шлюза — вызывающий код
        решает через тот же `_ModelBreaker`, что и сканер, см.
        `app/dish_recognition.py`);
      - ответ пришёл, но JSON не распарсился (`parse_json_object() is None`)
        -> `{}`, НЕ исключение — модель честно ответила чем-то нечитаемым,
        это не сбой шлюза (тот же принцип, что `parse_fields()` у
        `read_label_or_raise()` — пустой словарь на нечитаемый контент).
      - `_read_response_within_deadline()` переиспользуется как есть (то же
        чтение чанками по 1 байту, тот же смысл — см. её докстринг).

    Ключ шлюза не логируется и не несётся в исключении."""
    if not url:
        return {}
    try:
        img = prepare_full_frame_image(image_bytes, image_size)
    except Exception:  # noqa: BLE001 — битые байты: не сбой шлюза, вызывающий код решает сам
        return {}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ssl.create_default_context()) as resp:
            raw = _read_response_within_deadline(resp, deadline=time.monotonic() + timeout_s)
            payload = json.loads(raw)
        content = payload["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as exc:
        logger.warning("vision_llm: HTTP %s от шлюза (dish JSON)", exc.code)
        raise VisionLLMError(f"HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут/формат
        logger.warning("vision_llm: %s (dish JSON)", type(exc).__name__)
        raise VisionLLMError(type(exc).__name__) from exc
    return parse_json_object(content) or {}
