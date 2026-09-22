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
PROMPT = (
    "На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком. "
    "Прочитай этикетку и ответь строго одним JSON без пояснений: "
    '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. '
    "Русские надписи — кириллицей, латинские — латиницей. Чего не видно — пустая строка."
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
    авторизации). Ключ шлюза не логируется и не несётся в исключении."""
    if not url:
        return ""
    try:
        img = prepare_image(image_bytes, image_size)
    except Exception:  # noqa: BLE001 — битые байты: не сбой шлюза, пусть CV/OCR-путь решает сам
        return ""
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
    return fields_to_text(parse_fields(content))


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
