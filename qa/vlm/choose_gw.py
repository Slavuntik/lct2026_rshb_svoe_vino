"""Выбор вина среди кандидатов слияния через шлюз VLM → features/choose_<tag>.jsonl."""
import argparse, json, re, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gw import chat, image_b64

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
SUGAR_RU = {"suhoe": "сухое", "polusuhoe": "полусухое", "polusladkoe": "полусладкое", "sladkoe": "сладкое",
            "bryut": "брют", "ekstra bryut": "экстра брют", "desertnoe": "десертное"}
ap = argparse.ArgumentParser()
ap.add_argument("--cands", default="fused_top20_b384_maxall_gw27b.json")
ap.add_argument("--k", type=int, default=5)
ap.add_argument("--tag", default="gw_k5")
ap.add_argument("--model", default="qwen3.8-27b")
ap.add_argument("--workers", type=int, default=4)
a = ap.parse_args()
cands = json.loads((F / a.cands).read_text())
out = F / f"choose_{a.tag}.jsonl"
done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
photos = [p for p in json.loads((F / "photos.json").read_text()) if p not in done]
lock = threading.Lock()


def work(name):
    cs = cands[name][: a.k]
    lines = []
    for j, c in enumerate(cs, 1):
        extra = ", ".join(x for x in (c.get("category") or "", SUGAR_RU.get(c.get("sugar") or "", "")) if x)
        lines.append(f"{j}. {c.get('winery') or '?'} — {c.get('name') or '?'}" + (f" ({extra})" if extra else ""))
    prompt = ("На фото винные бутылки. Смотри на центральную бутылку, которая видна целиком, и внимательно "
              "прочитай её этикетку: винодельню, название, сорт, цвет, сахар. Какое вино из списка на ней?\n"
              + "\n".join(lines) + "\nОтветь только номером из списка. Если ни одно не подходит — 0.")
    raw, ms, _ = chat(a.model, prompt, image_b64(SRC / name, 1024), max_tokens=8)
    m = re.search(r"\d+", raw)
    choice = int(m.group(0)) if m else -1
    slug = cs[choice - 1]["slug"] if 1 <= choice <= len(cs) else cs[0]["slug"]
    with lock, out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"photo": name, "choice": choice, "slug": slug, "ms": ms, "raw": raw}, ensure_ascii=False) + "\n")


with ThreadPoolExecutor(a.workers) as ex:
    list(ex.map(work, photos))
print("готово:", out, len(photos))
