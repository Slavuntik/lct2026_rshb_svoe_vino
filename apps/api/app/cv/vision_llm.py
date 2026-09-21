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

    `key` необязателен: локальный сервер модели (`python -m mlx_vlm.server`) без авторизации."""
    if not url:
        return ""
    try:
        img = prepare_image(image_bytes, image_size)
    except Exception:  # noqa: BLE001 — битые байты: пусть CV/OCR-путь решает сам
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
            payload = json.loads(resp.read())
        content = payload["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as exc:
        logger.warning("vision_llm: HTTP %s от шлюза — фолбэк на OCR", exc.code)
        return ""
    except Exception as exc:  # noqa: BLE001 — сеть/таймаут/формат: фолбэк на OCR
        logger.warning("vision_llm: %s — фолбэк на OCR", type(exc).__name__)
        return ""
    return fields_to_text(parse_fields(content))
