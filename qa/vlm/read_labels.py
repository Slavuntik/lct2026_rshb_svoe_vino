"""Чтение этикеток реальных фото локальной VLM (MLX) → features/ocr_vlm*.jsonl.

Формат выхода как у OCR-признаков ({photo, text, ms} + raw), чтобы qa/real_photos_eval.py
сравнил VLM с PaddleOCR без доработок (--ocr vlm).
Запуск: .venv/bin/python read_labels.py [--model ...] [--size 1024] [--crop cwide|none] [--tag vlm] [--limit N]
"""
import argparse, json, re, time
from pathlib import Path

from PIL import Image, ImageOps

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
TMP = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/vlm-lab/tmp")
CROPS = {"cwide": (0.15, 0.05, 0.85, 0.98), "none": (0, 0, 1, 1)}

PROMPT = (
    "На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком. "
    "Прочитай её этикетку и ответь строго одним JSON без пояснений:\n"
    '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": "", "text": ""}\n'
    "winery — винодельня, name — название вина, grapes — сорта, color — красное/белое/розовое/оранжевое, "
    "sugar — сухое/полусухое/полусладкое/сладкое/брют/экстра брют, vintage — год урожая, "
    "text — весь читаемый текст этикетки. Пиши как на этикетке (кириллица или латиница). "
    "Чего не видно — пустая строка. Не выдумывай."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-VL-4B-Instruct-4bit")
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--crop", default="cwide", choices=list(CROPS))
    ap.add_argument("--tag", default="vlm")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=220)
    a = ap.parse_args()

    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    t0 = time.time()
    model, processor = load(a.model)
    config = load_config(a.model)
    prompt = apply_chat_template(processor, config, PROMPT, num_images=1)
    print(f"модель загружена за {time.time()-t0:.1f} с", flush=True)

    TMP.mkdir(parents=True, exist_ok=True)
    out = F / f"ocr_{a.tag}.jsonl"
    done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    photos = json.loads((F / "photos.json").read_text())
    if a.limit:
        photos = photos[: a.limit]
    with out.open("a", encoding="utf-8") as fh:
        for i, name in enumerate(photos, 1):
            if name in done:
                continue
            im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB")
            w, h = im.size
            x0, y0, x1, y1 = CROPS[a.crop]
            im = im.crop((int(w * x0), int(h * y0), int(w * x1), int(h * y1)))
            im.thumbnail((a.size, a.size))
            tmp = TMP / f"q_{a.tag}.jpg"
            im.save(tmp, quality=92)
            t = time.time()
            res = generate(model, processor, prompt, image=[str(tmp)], max_tokens=a.max_tokens,
                           temperature=0.0, verbose=False)
            ms = round((time.time() - t) * 1000)
            raw = res if isinstance(res, str) else getattr(res, "text", str(res))
            parsed = {}
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = {}
            fields = [str(parsed.get(k) or "") for k in ("winery", "name", "grapes", "color", "sugar", "vintage", "text")]
            text = " ".join(f for f in fields if f) if parsed else raw
            fh.write(json.dumps({"photo": name, "text": text, "ms": ms, "raw": raw}, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 10 == 0 or i <= 3:
                print(f"{i}/{len(photos)} {ms} мс | {text[:100]}", flush=True)
    print("готово:", out)


if __name__ == "__main__":
    main()
