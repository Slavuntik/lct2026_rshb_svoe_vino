"""Текстовая сверка (без картинки): поля, прочитанные VLM, против карточки top-1 → да/нет."""
import json, re, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gw import chat

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SUGAR_RU = {"suhoe": "сухое", "polusuhoe": "полусухое", "polusladkoe": "полусладкое", "sladkoe": "сладкое",
            "bryut": "брют", "ekstra bryut": "экстра брют", "desertnoe": "десертное"}
fused = json.loads((F / "fused_top20_b384_maxall_gw27b.json").read_text())
gw = {json.loads(l)["photo"]: json.loads(l) for l in (F / "ocr_gw27b.jsonl").read_text().splitlines()}
out = F / "verify_text_gw.jsonl"
lock = threading.Lock()


def fields(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    return {k: d.get(k, "") for k in ("winery", "name", "grapes", "color", "sugar", "vintage")}


def work(p):
    c = fused[p][0]
    read = fields(gw[p]["raw"])
    cand = {"winery": c.get("winery"), "name": c.get("name"), "color": c.get("category"),
            "sugar": SUGAR_RU.get(c.get("sugar") or "", "")}
    prompt = ("С этикетки прочитано: " + json.dumps(read, ensure_ascii=False) +
              "\nКарточка каталога: " + json.dumps(cand, ensure_ascii=False) +
              "\nЭто одно и то же вино? Другой год урожая допустим. Учитывай транслитерацию и сокращения. "
              "Если винодельня или название явно другие — нет. Ответь одним словом: да или нет.")
    raw, ms, _ = chat("qwen3.8-27b", prompt, None, max_tokens=4)
    with lock, out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"photo": p, "answer": raw.strip().lower(), "ms": ms}, ensure_ascii=False) + "\n")


if out.exists():
    out.unlink()
with ThreadPoolExecutor(4) as ex:
    list(ex.map(work, list(fused)))
print("готово", out)
