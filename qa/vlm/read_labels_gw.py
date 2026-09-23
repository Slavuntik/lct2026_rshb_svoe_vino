"""Чтение этикеток реальных фото через шлюз (OpenAI-совместимый) → features/ocr_<tag>.jsonl."""
import argparse, json, re, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gw import chat, image_b64

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
PROMPT_FULL = (
    "На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком. "
    "Прочитай её этикетку и ответь строго одним JSON без пояснений:\n"
    '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": "", "text": ""}\n'
    "winery — винодельня, name — название вина, grapes — сорта, color — красное/белое/розовое/оранжевое, "
    "sugar — сухое/полусухое/полусладкое/сладкое/брют/экстра брют, vintage — год урожая, "
    "text — весь читаемый текст этикетки. Пиши как на этикетке: русские надписи — кириллицей, "
    "латинские — латиницей. Чего не видно — пустая строка. Не выдумывай."
)
PROMPT_FIELDS = (
    "На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком. "
    "Прочитай этикетку и ответь строго одним JSON без пояснений: "
    '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. '
    "Русские надписи — кириллицей, латинские — латиницей. Чего не видно — пустая строка."
)
ap = argparse.ArgumentParser()
ap.add_argument("--model", default="qwen3.8-27b")
ap.add_argument("--tag", default="gw27b")
ap.add_argument("--size", type=int, default=1024)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--prompt", default="full", choices=["full", "fields"])
a = ap.parse_args()
out = F / f"ocr_{a.tag}.jsonl"
done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
photos = [p for p in json.loads((F / "photos.json").read_text()) if p not in done]
lock = threading.Lock()


def work(name):
    prompt = PROMPT_FULL if a.prompt == "full" else PROMPT_FIELDS
    raw, ms, usage = chat(a.model, prompt, image_b64(SRC / name, a.size), max_tokens=300 if a.prompt == "full" else 120)
    parsed = {}
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    fields = [str(parsed.get(k) or "") for k in ("winery", "name", "grapes", "color", "sugar", "vintage", "text")]
    text = " ".join(f for f in fields if f) if parsed else raw
    with lock, out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"photo": name, "text": text, "ms": ms, "raw": raw}, ensure_ascii=False) + "\n")


with ThreadPoolExecutor(a.workers) as ex:
    list(ex.map(work, photos))
print("готово:", out, "фото:", len(photos))
