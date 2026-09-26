"""Offline, explicitly invoked shelf VLM audit through a configured LiteLLM gateway.

Only supplied images/candidate names are sent. No annotation file is read.
Credentials come from environment or a local dotenv file; never saved in reports.
"""

import argparse
import base64
import io
import json
import os
from pathlib import Path
import time

import httpx
from PIL import Image, ImageDraw, ImageOps

FULL_PROMPT = """Найди отдельные винные бутылки на фото полки и прочитай видимый текст этикеток.
Не переписывай ценники. Не восстанавливай нечитаемые слова по памяти о бренде.
Верни JSON {"bottles":[{"box":[x1,y1,x2,y2],"text":"видимый текст","name":"название если читается"}]}.
Координаты рамки всей бутылки — целые числа от 0 до 1000 относительно всего изображения.
Соседние бутылки не объединяй. Если текст не читается, оставь text и name пустыми.
Не добавляй пояснения за пределами JSON."""
CROP_PROMPT = """Это пронумерованные вырезки отдельных бутылок. Номер над каждой вырезкой — её id.
Прочитай только видимые надписи этикетки. Не угадывай по памяти, не добавляй нечитаемые слова.
Верни JSON {"bottles":[{"crop_id":1,"text":"видимый текст","name":"видимое название"}]}.
Дай запись для каждой вырезки; если текст не читается, поля пустые. Только JSON."""
GUIDED_PROMPT = CROP_PROMPT + """
Для каждой вырезки ниже даны кандидаты каталога. Это гипотезы, они могут ВСЕ быть неверны.
Добавь "selected_id": точный id кандидата или null, и "evidence": видимый различающий текст.
Выбирай только при совпадении читаемого названия/серии и различающих признаков (сорт, цвет, сахар).
Общего бренда или похожего дизайна недостаточно. Не переноси слова из списка кандидатов в OCR.
При неоднозначности оставь selected_id=null. Кандидаты:\n"""


def parse_response(content):
    if not isinstance(content, str):
        raise ValueError("Expected text response")
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(content)
    if isinstance(data, list):
        data = {"bottles": data}
    if not isinstance(data, dict) or not isinstance(data.get("bottles"), list):
        raise ValueError("Expected bottles array")
    return data


def select_crops(row, limit):
    # Detector/recognizer outputs only; no human labels or expected product IDs.
    accepted = [o for o in row["observations"] if o.get("id")][:4]
    uncertain = sorted(
        [
            o
            for o in row["observations"]
            if not o.get("id") and o.get("rescueQueueRank")
        ],
        key=lambda o: o["rescueQueueRank"],
    )
    return (accepted + uncertain)[:limit]


def contact_sheet(image, observations):
    cols, cw, ch = 4, 240, 360
    canvas = Image.new(
        "RGB", (cols * cw, ((len(observations) + cols - 1) // cols) * ch), "white"
    )
    draw = ImageDraw.Draw(canvas)
    for i, item in enumerate(observations):
        x1, y1, x2, y2 = item["box"]
        crop = image.crop(
            (
                int(x1 * image.width),
                int(y1 * image.height),
                int(x2 * image.width),
                int(y2 * image.height),
            )
        )
        crop.thumbnail((cw - 12, ch - 40))
        # Enlarge small source crops for legibility of the sheet, not recovered detail.
        scale = min((cw - 12) / crop.width, (ch - 40) / crop.height)
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
        )
        x, y = (i % cols) * cw, (i // cols) * ch
        draw.text((x + 8, y + 8), str(i + 1), fill="black")
        canvas.paste(crop, (x + (cw - crop.width) // 2, y + 32))
    return canvas


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path)
    p.add_argument("--photos", type=Path, required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--model", default="qwen3.8-27b")
    p.add_argument(
        "--mode", choices=["full", "crops", "guided", "guided-fields"], default="full"
    )
    p.add_argument("--native-results", type=Path)
    p.add_argument("--catalog", type=Path)
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.limit <= 24:
        p.error("--limit must be between 1 and 24")
    if a.mode != "full" and not a.native_results:
        p.error("crop modes require --native-results")
    if a.mode.startswith("guided") and not a.catalog:
        p.error("guided mode requires --catalog")
    config = {}
    if a.env_file:
        from dotenv import dotenv_values

        config.update(dotenv_values(a.env_file))
    config.update(os.environ)
    url = (config.get("VISION_LLM_URL") or config.get("LLM_BASE_URL", "")).rstrip("/")
    key = config.get("VISION_LLM_KEY") or config.get("LLM_API_KEY", "")
    if not url or not key:
        p.error("Configure gateway URL and key in environment or --env-file")
    native = (
        {r["file"]: r for r in json.loads(a.native_results.read_text())["rows"]}
        if a.native_results
        else {}
    )
    catalog = (
        {r["slug"]: r for r in map(json.loads, a.catalog.read_text().splitlines())}
        if a.catalog
        else {}
    )
    result = {
        "model": a.model,
        "mode": a.mode,
        "manualReview": False,
        "limit": a.limit,
        "rows": [],
    }
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        timeout=a.timeout, headers={"Authorization": "Bearer " + key}
    ) as client:
        for filename in a.files:
            with Image.open(a.photos / filename) as raw:
                image = ImageOps.exif_transpose(raw).convert("RGB")
            observations = []
            prompt = FULL_PROMPT
            if a.mode != "full":
                observations = select_crops(native[filename], a.limit)
                image = contact_sheet(image, observations)
                prompt = CROP_PROMPT
                if a.mode.startswith("guided"):
                    candidates = []
                    for i, o in enumerate(observations):
                        candidates.append(
                            {
                                "crop_id": i + 1,
                                "candidates": [
                                    {
                                        "id": s,
                                        "name": catalog[s]["name"],
                                        "winery": catalog[s].get("winery"),
                                        **(
                                            {
                                                k: catalog[s].get(k)
                                                for k in (
                                                    "color",
                                                    "grapes",
                                                    "attributes",
                                                )
                                            }
                                            if a.mode == "guided-fields"
                                            else {}
                                        ),
                                    }
                                    for s in o.get("semanticCandidates", [])[:5]
                                    if s in catalog
                                ],
                            }
                        )
                    prompt = GUIDED_PROMPT + json.dumps(candidates, ensure_ascii=False)
                    if a.mode == "guided-fields":
                        prompt += "\nСравни различия всех кандидатов. Одноимённые вина разных цветов — разные SKU. Название сорта (например Cabernet Franc) не подтверждает производителя. Не выбирай винодельню без читаемого бренда/серии. Цвет вина через стекло и освещение ненадёжен: ищи слова rose/blanc/розовое/белое. Если различие не видно, selected_id=null."
            image.save(
                a.output.parent
                / (a.output.stem + "-" + Path(filename).stem + "-input.jpg"),
                quality=95,
            )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
            body = {
                "model": a.model,
                "temperature": 0,
                "max_tokens": 5000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64,"
                                    + base64.b64encode(buffer.getvalue()).decode()
                                },
                            },
                        ],
                    }
                ],
            }
            row = {
                "file": filename,
                "inputSize": list(image.size),
                "prompt": prompt,
                "crops": [
                    {
                        "crop_id": i + 1,
                        "box": o["box"],
                        "candidates": o.get("semanticCandidates", [])[:5],
                    }
                    for i, o in enumerate(observations)
                ],
            }
            start = time.perf_counter()
            try:
                response = client.post(url + "/chat/completions", json=body)
                row["httpStatus"] = response.status_code
                if response.is_success:
                    payload = response.json()
                    choice = payload["choices"][0]
                    row.update(
                        content=choice["message"].get("content", ""),
                        finishReason=choice.get("finish_reason"),
                        usage=payload.get("usage"),
                        servedModel=payload.get("model"),
                    )
                    try:
                        row["parsed"] = parse_response(row["content"])
                    except (ValueError, TypeError):
                        row["parseError"] = True
                else:
                    row["error"] = "HTTP " + str(response.status_code)
            except httpx.HTTPError as error:
                row["error"] = type(error).__name__
            except (ValueError, KeyError, TypeError, IndexError):
                row["error"] = "malformed_gateway_response"
            row["seconds"] = round(time.perf_counter() - start, 3)
            result["rows"].append(row)
            temp = a.output.with_suffix(".tmp")
            temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            temp.replace(a.output)
            print(
                filename,
                a.mode,
                row["seconds"],
                row.get("error", len(row.get("parsed", {}).get("bottles", []))),
                flush=True,
            )


if __name__ == "__main__":
    main()
