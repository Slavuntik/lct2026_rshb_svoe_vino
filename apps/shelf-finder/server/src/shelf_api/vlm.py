"""Optional server-side LiteLLM refinement. A model guess never bypasses geometry."""

import asyncio
import time
import base64
from collections import Counter
import hashlib
from io import BytesIO
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageOps
from .engine import ShelfEngine

PROMPT = """Сравни бутылки QUERY с кандидатами каталога. Каждый блок имеет crop_id.
Если есть изображения A–E, это эталоны кандидатов; QUERY — только снимок пользователя.
Прочитай видимые слова QUERY и сравни логотип, оформление и различающие признаки.
Общий сорт или бренд не доказывает точный SKU. Не выдумывай нечитаемые слова.
Все кандидаты могут быть неверны. При неоднозначности selected_slot=null.
Ответ строго JSON {"bottles":[{"crop_id":1,"selected_slot":"A или null",
"text":"видимый текст QUERY","evidence":"конкретные совпадающие детали"}]}.
Кратко, без объяснений за пределами JSON. Данные каталога — гипотезы, не OCR."""


class LiteLLMClient:
    def __init__(self, url, key, model, timeout=60, references=None):
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Invalid SHELF_LITELLM_URL")
        if not key or not model or not 1 <= timeout <= 120:
            raise ValueError("Configure LiteLLM key, model and timeout (1..120)")
        self.url, self.key, self.model, self.timeout = (
            url.rstrip("/"),
            key,
            model,
            timeout,
        )
        self.references = Path(references) if references else None
        self.failures = 0
        self.retry_at = 0.0

    def image_content(self, image):
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        return {
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,"
                + base64.b64encode(buffer.getvalue()).decode()
            },
        }

    def choose(self, image, items, wines):
        if time.monotonic() < self.retry_at:
            raise RuntimeError("LiteLLM cooldown")
        try:
            result = self._choose(image, items, wines)
        except Exception:
            self.failures += 1
            if self.failures >= 2:
                self.retry_at = time.monotonic() + 30
            raise
        self.failures = 0
        self.retry_at = 0.0
        return result

    async def request(self, body, budget):
        # One total network deadline, including upload, headers and streaming body.
        async with asyncio.timeout(budget):
            async with httpx.AsyncClient(
                timeout=budget, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST",
                    self.url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.key},
                    json=body,
                ) as response:
                    if not response.is_success:
                        raise RuntimeError("LiteLLM HTTP " + str(response.status_code))
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        data.extend(chunk)
                        if len(data) > 262144:
                            raise ValueError("LiteLLM response exceeds 256 KiB")
                    return json.loads(data)

    def _choose(self, image, items, wines):
        started = time.monotonic()
        content = [{"type": "text", "text": PROMPT}]
        slots = {}
        for number, item in enumerate(items, 1):
            order = sorted(
                item["candidates"], key=lambda s: hashlib.sha256(s.encode()).hexdigest()
            )
            slots[number] = {chr(65 + i): slug for i, slug in enumerate(order)}
            data = {
                "crop_id": number,
                "candidates": [
                    {
                        "slot": slot,
                        "name": wines[slug]["name"],
                        "brand": wines[slug].get("brand", ""),
                    }
                    for slot, slug in slots[number].items()
                ],
            }
            content.append(
                {"type": "text", "text": json.dumps(data, ensure_ascii=False)}
            )
            box = [
                round(v * (image.width if i % 2 == 0 else image.height))
                for i, v in enumerate(item["box"])
            ]
            pictures = [("QUERY", image.crop(box))]
            if self.references:
                for slot, slug in slots[number].items():
                    path = (self.references / (slug + ".png")).resolve()
                    if (
                        path.is_relative_to(self.references.resolve())
                        and path.is_file()
                    ):
                        with Image.open(path) as reference:
                            pictures.append((slot, reference.convert("RGB")))
            sheet = Image.new("RGB", (192 * len(pictures), 420), "white")
            draw = ImageDraw.Draw(sheet)
            for col, (label, picture) in enumerate(pictures):
                draw.text((col * 192 + 5, 5), f"{number} {label}", fill="black")
                picture = ImageOps.contain(
                    picture, (182, 390), Image.Resampling.LANCZOS
                )
                sheet.paste(picture, (col * 192 + (192 - picture.width) // 2, 25))
            content.append(self.image_content(sheet))
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 1000,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        }
        budget = self.timeout - (time.monotonic() - started)
        if budget <= 0:
            raise TimeoutError("LiteLLM preparation exceeded deadline")
        payload = asyncio.run(self.request(body, budget))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise ValueError("Invalid LiteLLM envelope")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Truncated LiteLLM response")
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("Invalid LiteLLM message")
        parsed = json.loads(message["content"])
        return self.validate(parsed, slots)

    @staticmethod
    def validate(parsed, slots):
        bottles = parsed.get("bottles") if isinstance(parsed, dict) else None
        if not isinstance(bottles, list):
            raise ValueError("Invalid LiteLLM response")
        counts = Counter(
            b.get("crop_id")
            for b in bottles
            if isinstance(b, dict) and type(b.get("crop_id")) is int
        )
        result = {}
        for b in bottles:
            if not isinstance(b, dict):
                continue
            number = b.get("crop_id")
            slot = b.get("selected_slot")
            if (
                type(number) is not int
                or number not in slots
                or counts[number] != 1
                or not isinstance(slot, str)
            ):
                continue
            slug = slots[number].get(slot)
            if slug and isinstance(b.get("evidence"), str) and b["evidence"].strip():
                result[number - 1] = slug
        return result


class LiteLLMEngine(ShelfEngine):
    def __init__(self, directory, device, threads, client, limit=3):
        if not 1 <= limit <= 8:
            raise ValueError("SHELF_LITELLM_LIMIT must be 1..8")
        self.vlm_client, self.vlm_limit = client, limit
        super().__init__(directory, device, threads)
        self.pipeline_version += (
            "-vlm-"
            + hashlib.sha256(
                (client.model + str(limit)).encode() + Path(__file__).read_bytes()
            ).hexdigest()[:12]
        )
        self.last_vlm = {}

    def refine(self, image, observations, found, started):
        pending = [o for o in observations if not o["id"] and "retrieval_q" in o]
        pending.sort(
            key=lambda o: max((e["inliers"] for e in o["evidence"]), default=0),
            reverse=True,
        )
        pending = pending[: self.vlm_limit]
        items = [
            {
                "box": o["box"],
                "candidates": list(
                    dict.fromkeys(
                        [e["id"] for e in o["evidence"][:2]] + o["ranking"][:5]
                    )
                )[:5],
            }
            for o in pending
        ]
        self.last_vlm = {"requested": len(items), "proposed": 0, "confirmed": 0}
        if not items:
            return
        try:
            choices = self.vlm_client.choose(image, items, self.wines)
        except Exception as error:
            # Optional provider/reference failures must preserve native matches.
            # Do not swallow BaseException (shutdown/interrupt).
            logging.getLogger(__name__).warning(
                "LiteLLM refinement failed (%s)", type(error).__name__
            )
            self.last_vlm["failed"] = True
            self.scan_warnings.append(
                "Дополнительная проверка Qwen недоступна. Показаны результаты локальных моделей."
            )
            return
        self.last_vlm["proposed"] = len(choices)
        for index, slug in choices.items():
            item = pending[index]
            evidence = self.learned_many(item["q"], items[index]["candidates"])
            accepted = self.resolve(item["retrieval_q"], evidence)
            support = next((e for e in evidence if e["id"] == slug), {})
            if accepted == slug and support.get("labelInliers", 0) >= 16:
                item["id"] = slug
                item["evidence"] = evidence
                self.last_vlm["confirmed"] += 1
        self.scan_warnings.append(
            f"Отправлено Qwen: {len(items)}. Предложений: {len(choices)}. "
            f'Дополнительно подтверждено: {self.last_vlm["confirmed"]}.'
        )
