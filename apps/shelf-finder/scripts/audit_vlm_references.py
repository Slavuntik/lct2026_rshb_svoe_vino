"""Qwen shelf verification against actual catalog photographs, in small batches.

Uses frozen detector candidates, never evaluation labels. Remote/billable calls
are made only when this command is explicitly run. Results remain experimental.
"""

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import time

import httpx
from PIL import Image, ImageDraw, ImageOps
from dotenv import dotenv_values
from audit_vlm import parse_response, select_crops

PROMPT = """Сравни фото бутылок с эталонами каталога. В каждой строке QUERY — реальная
бутылка с полки, A–E — фотографии кандидатов. Верх каждой колонки — бутылка,
низ — увеличенная нижняя часть этикетки. Номер строки — crop_id.
Эталоны могут ВСЕ не соответствовать запросу. Порядок эталонов случайный.
Определи совпадение конкретной этикетки: сравни логотип, рисунок, расположение
надписей, цвет этикетки и читаемые различающие слова. Одного общего бренда
или сорта недостаточно. Цвет жидкости через стекло ненадёжен. Если надпись,
оформление или серия противоречат эталону — отклони его. Изменение ракурса,
блик и частичное перекрытие допустимы, но не выдумывай скрытые детали.
Данные каталога ниже — только гипотезы; не выдавай их текст за прочитанный.
Верни JSON {"bottles":[{"crop_id":1,"text":"только видимый текст QUERY",
"selected_slot":"A или B или C или D или E либо null",
"evidence":"конкретные совпадающие детали QUERY и эталона",
"difference":"чем выбранный отличается от ближайшего конкурента"}]}.
Выбирай только если различие с похожими конкурентами действительно видно.
Неуверенность/неразличимые версии/совпадение только общего бренда — null.
"""


def candidate_order(slugs, filename, crop_id):
    return sorted(
        set(slugs),
        key=lambda s: hashlib.sha256(f"{filename}:{crop_id}:{s}".encode()).hexdigest(),
    )


def paste_fit(canvas, image, bounds):
    left, top, right, bottom = bounds
    image = ImageOps.contain(
        image, (right - left, bottom - top), Image.Resampling.LANCZOS
    )
    canvas.paste(
        image,
        (
            left + (right - left - image.width) // 2,
            top + (bottom - top - image.height) // 2,
        ),
    )


def reference_strip(query, references, crop_id):
    cw, ch = 192, 500
    canvas = Image.new("RGB", (cw * (len(references) + 1), ch), "white")
    draw = ImageDraw.Draw(canvas)
    for i, im in enumerate([query] + references):
        left = i * cw
        draw.text(
            (left + 8, 8), f"{crop_id} QUERY" if i == 0 else chr(64 + i), fill="black"
        )
        paste_fit(canvas, im, (left + 6, 30, left + cw - 6, 300))
        label = (
            im.crop((0, round(im.height * 0.4), im.width, round(im.height * 0.97)))
            if im.height / im.width >= 1.8
            else im
        )
        draw.line((left, 310, left + cw, 310), fill="gray")
        paste_fit(canvas, label, (left + 6, 320, left + cw - 6, ch - 6))
    return canvas


def convert_response(parsed, slots):
    bottles = []
    for b in parsed["bottles"]:
        if not isinstance(b, dict):
            continue
        cid = b.get("crop_id")
        if type(cid) is not int or cid not in slots:
            continue
        slot = b.get("selected_slot")
        chosen = slots[cid].get(slot) if isinstance(slot, str) else None
        # Evidence must explain both support and discrimination; not confidence prose alone.
        if not isinstance(b.get("difference"), str) or not b["difference"].strip():
            chosen = None
        bottles.append({**b, "selected_id": chosen})
    return bottles


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--photos", type=Path, required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--native-results", type=Path, required=True)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--references", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default="qwen3.8-27b")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=3)
    a = p.parse_args()
    if not 1 <= a.limit <= 24 or not 1 <= a.batch_size <= 4:
        p.error("limit 1..24, batch-size 1..4")
    config = {**dotenv_values(a.env_file), **os.environ}
    url = (config.get("VISION_LLM_URL") or config.get("LLM_BASE_URL", "")).rstrip("/")
    key = config.get("VISION_LLM_KEY") or config.get("LLM_API_KEY", "")
    if not url or not key:
        p.error("Missing gateway configuration")
    native = {r["file"]: r for r in json.loads(a.native_results.read_text())["rows"]}
    catalog = {
        r["slug"]: r for r in map(json.loads, a.catalog.read_text().splitlines())
    }
    result = {
        "mode": "guided-references",
        "model": a.model,
        "manualReview": False,
        "batchSize": a.batch_size,
        "limit": a.limit,
        "rows": [],
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        temp = a.output.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        temp.replace(a.output)

    with httpx.Client(
        timeout=120, headers={"Authorization": "Bearer " + key}
    ) as client:
        for filename in a.files:
            with Image.open(a.photos / filename) as im:
                image = ImageOps.exif_transpose(im).convert("RGB")
            crops = select_crops(native[filename], a.limit)
            row = {
                "file": filename,
                "crops": [
                    {
                        "crop_id": i + 1,
                        "box": c["box"],
                        "candidates": c.get("semanticCandidates", [])[:5],
                    }
                    for i, c in enumerate(crops)
                ],
                "parsed": {"bottles": []},
                "requests": [],
                "seconds": 0,
                "complete": False,
            }
            result["rows"].append(row)
            for offset in range(0, len(crops), a.batch_size):
                strips = []
                metadata = []
                slots = {}
                for i in range(offset, min(offset + a.batch_size, len(crops))):
                    crop = crops[i]
                    cid = i + 1
                    box = [
                        round(v * (image.width if j % 2 == 0 else image.height))
                        for j, v in enumerate(crop["box"])
                    ]
                    order = candidate_order(
                        crop.get("semanticCandidates", [])[:5], filename, cid
                    )
                    order = [
                        s
                        for s in order
                        if s in catalog and (a.references / (s + ".png")).is_file()
                    ]
                    refs = []
                    for s in order:
                        with Image.open(a.references / (s + ".png")) as ref:
                            refs.append(ref.convert("RGB"))
                    strips.append(reference_strip(image.crop(box), refs, cid))
                    slots[cid] = {chr(65 + j): s for j, s in enumerate(order)}
                    metadata.append(
                        {
                            "crop_id": cid,
                            "candidates": [
                                {
                                    "slot": slot,
                                    **{
                                        k: catalog[s].get(k)
                                        for k in (
                                            "name",
                                            "winery",
                                            "color",
                                            "grapes",
                                            "attributes",
                                        )
                                    },
                                }
                                for slot, s in slots[cid].items()
                            ],
                        }
                    )
                sheet = Image.new(
                    "RGB",
                    (max(s.width for s in strips), sum(s.height for s in strips)),
                    "white",
                )
                y = 0
                for strip in strips:
                    sheet.paste(strip, (0, y))
                    y += strip.height
                input_path = (
                    a.output.parent
                    / f"{a.output.stem}-{Path(filename).stem}-{offset//a.batch_size+1}.jpg"
                )
                sheet.save(input_path, quality=95)
                buf = io.BytesIO()
                sheet.save(buf, format="JPEG", quality=95)
                prompt = PROMPT + json.dumps(metadata, ensure_ascii=False)
                body = {
                    "model": a.model,
                    "temperature": 0,
                    "max_tokens": 1600,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ответ строго JSON с ключом bottles. Без вступления и анализа. Поля evidence и difference краткие, максимум по 15 слов.",
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/jpeg;base64,"
                                        + base64.b64encode(buf.getvalue()).decode()
                                    },
                                },
                            ],
                        },
                    ],
                }
                request = {
                    "cropIds": list(slots),
                    "slots": slots,
                    "prompt": prompt,
                    "inputSha256": hashlib.sha256(buf.getvalue()).hexdigest(),
                }
                start = time.perf_counter()
                try:
                    response = client.post(url + "/chat/completions", json=body)
                    request["httpStatus"] = response.status_code
                    if response.is_success:
                        payload = response.json()
                        choice = payload["choices"][0]
                        request.update(
                            content=choice["message"].get("content"),
                            usage=payload.get("usage"),
                            finishReason=choice.get("finish_reason"),
                            servedModel=payload.get("model"),
                        )
                        parsed = parse_response(request["content"])
                        row["parsed"]["bottles"].extend(convert_response(parsed, slots))
                    else:
                        request["error"] = "HTTP " + str(response.status_code)
                except httpx.HTTPError as error:
                    request["error"] = type(error).__name__
                except (ValueError, KeyError, TypeError, IndexError):
                    request["error"] = "malformed_response"
                request["seconds"] = round(time.perf_counter() - start, 3)
                row["seconds"] = round(row["seconds"] + request["seconds"], 3)
                row["requests"].append(request)
                save()
                print(
                    filename,
                    offset + 1,
                    request["seconds"],
                    request.get("error", "ok"),
                    flush=True,
                )
            row["complete"] = True
            save()


if __name__ == "__main__":
    main()
