"""Клиент OpenAI-совместимого шлюза (LiteLLM) для опытов с этикетками. Ключ — из vlm-lab/.env."""
import base64, io, json, os, ssl, time, urllib.request
from pathlib import Path

from PIL import Image, ImageOps

# Файл с адресом/ключом шлюза — ВНЕ репозитория (секрет): путь из VLM_GATEWAY_ENV,
# по умолчанию vines/vlm-lab/.env (chmod 600). Формат: LLM_GATEWAY_URL=..., LLM_GATEWAY_KEY=...
ENV = Path(os.environ.get("VLM_GATEWAY_ENV", "/Users/vyacheslavfokin/ClaudeWorkspace/vines/vlm-lab/.env"))
for line in ENV.read_text().splitlines():
    if "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
URL = os.environ["LLM_GATEWAY_URL"].rstrip("/")
KEY = os.environ["LLM_GATEWAY_KEY"]
CTX = ssl.create_default_context()  # у шлюза публичный сертификат Let's Encrypt на IP — проверяем штатно


def image_b64(path, size=1024, crop=(0.15, 0.05, 0.85, 0.98)):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = im.size
    im = im.crop((int(w * crop[0]), int(h * crop[1]), int(w * crop[2]), int(h * crop[3])))
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def chat(model, prompt, img_b64=None, max_tokens=300, timeout=60, temperature=0.0, extra=None):
    content = [{"type": "text", "text": prompt}]
    if img_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": temperature}
    if extra:
        body.update(extra)
    req = urllib.request.Request(f"{URL}/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            d = json.loads(r.read())
        msg = d["choices"][0]["message"]
        return (msg.get("content") or "").strip(), round((time.time() - t) * 1000), d.get("usage")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read()[:300]!r}", round((time.time() - t) * 1000), None
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}", round((time.time() - t) * 1000), None
