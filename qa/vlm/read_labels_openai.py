"""Чтение этикеток любой моделью за OpenAI-совместимым /v1 (Ollama, llama-server, mlx_vlm.server) → features/ocr_<tag>.jsonl.
Тот же промпт (только поля) и тот же кроп центральной бутылки, что у Qwen — честное сравнение."""
import argparse, base64, io, json, re, time, urllib.request
from pathlib import Path
from PIL import Image, ImageOps

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
PROMPT = ("На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком. "
          "Прочитай этикетку и ответь строго одним JSON без пояснений: "
          '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. '
          "Русские надписи — кириллицей, латинские — латиницей. Чего не видно — пустая строка.")
ap = argparse.ArgumentParser()
ap.add_argument("--model", default="gemma3:4b")
ap.add_argument("--tag", default="gemma3_4b")
ap.add_argument("--url", default="http://localhost:11434/v1")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--images-dir", default="", help="готовые кропы NNN.jpg (порядок photos.json) вместо центрального кропа кадра")
ap.add_argument("--size", type=int, default=1024, help="длинная сторона кропа центральной бутылки")
ap.add_argument("--max-tokens", type=int, default=150)
a = ap.parse_args()
out = F / f"ocr_{a.tag}.jsonl"
done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
ALL = json.loads((F / "photos.json").read_text())
photos = [p for p in ALL if p not in done]
if a.limit:
    photos = photos[: a.limit]
for name in photos:
    if a.images_dir:
        im = Image.open(Path(a.images_dir) / f"{ALL.index(name) + 1:03d}.jpg").convert("RGB")
    else:
        im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB"); w, h = im.size
        im = im.crop((int(w * .15), int(h * .05), int(w * .85), int(h * .98)))
    im.thumbnail((a.size, a.size))
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=90)
    body = {"model": a.model, "temperature": 0, "max_tokens": a.max_tokens, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}]}
    t = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(a.url + "/chat/completions", data=json.dumps(body).encode(),
                                                           headers={"Content-Type": "application/json"}), timeout=180) as r:
            raw = json.loads(r.read())["choices"][0]["message"]["content"] or ""
    except Exception as e:  # noqa: BLE001
        raw = f"{type(e).__name__}: {e}"
    ms = round((time.time() - t) * 1000)
    m = re.search(r"\{.*\}", raw, re.S); d = {}
    if m:
        try: d = json.loads(m.group(0))
        except json.JSONDecodeError: d = {}
    text = " ".join(str(d.get(k) or "") for k in ("winery", "name", "grapes", "color", "sugar", "vintage") if d.get(k)) if d else ""
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"photo": name, "text": text, "ms": ms, "raw": raw}, ensure_ascii=False) + "\n")
print("готово:", out, len(photos))
