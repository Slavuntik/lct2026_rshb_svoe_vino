"""VLM выбирает вино среди кандидатов слияния CV+текст (features/fused_top20_<kind>.json).

Выход: features/choose_<tag>.jsonl {photo, choice (1..K или 0), slug (выбранный или fused top-1), ms, raw}.
Запуск: .venv/bin/python choose.py --cands fused_top20_b384_maxall.json --k 8 --tag ch8
"""
import argparse, json, re, time
from pathlib import Path

from PIL import Image, ImageOps

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
TMP = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/vlm-lab/tmp")
SUGAR_RU = {"suhoe": "сухое", "polusuhoe": "полусухое", "polusladkoe": "полусладкое", "sladkoe": "сладкое",
            "bryut": "брют", "ekstra bryut": "экстра брют", "desertnoe": "десертное"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mlx-community/Qwen3-VL-4B-Instruct-4bit")
    ap.add_argument("--cands", default="fused_top20_b384_maxall.json")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--tag", default="ch8")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    model, processor = load(a.model)
    config = load_config(a.model)
    cands = json.loads((F / a.cands).read_text())
    photos = json.loads((F / "photos.json").read_text())
    if a.limit:
        photos = photos[: a.limit]
    TMP.mkdir(parents=True, exist_ok=True)
    out = F / f"choose_{a.tag}.jsonl"
    done = {json.loads(l)["photo"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    with out.open("a", encoding="utf-8") as fh:
        for i, name in enumerate(photos, 1):
            if name in done:
                continue
            cs = cands[name][: a.k]
            lines = []
            for j, c in enumerate(cs, 1):
                extra = ", ".join(x for x in (c.get("category") or "", SUGAR_RU.get(c.get("sugar") or "", "")) if x)
                lines.append(f"{j}. {c.get('winery') or '?'} — {c.get('name') or '?'}" + (f" ({extra})" if extra else ""))
            prompt_text = (
                "На фото винные бутылки. Смотри только на центральную бутылку, которая видна целиком, "
                "и прочитай её этикетку. Какое вино из списка на ней?\n" + "\n".join(lines) +
                "\nОтветь только номером из списка. Если ни одно не подходит — 0."
            )
            prompt = apply_chat_template(processor, config, prompt_text, num_images=1)
            im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB")
            w, h = im.size
            im = im.crop((int(w * 0.15), int(h * 0.05), int(w * 0.85), int(h * 0.98)))
            im.thumbnail((a.size, a.size))
            tmp = TMP / f"c_{a.tag}.jpg"
            im.save(tmp, quality=92)
            t = time.time()
            res = generate(model, processor, prompt, image=[str(tmp)], max_tokens=6, temperature=0.0, verbose=False)
            ms = round((time.time() - t) * 1000)
            raw = res if isinstance(res, str) else getattr(res, "text", str(res))
            m = re.search(r"\d+", raw)
            choice = int(m.group(0)) if m else -1
            slug = cs[choice - 1]["slug"] if 1 <= choice <= len(cs) else cs[0]["slug"]
            fh.write(json.dumps({"photo": name, "choice": choice, "slug": slug, "ms": ms, "raw": raw}, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 10 == 0 or i <= 2:
                print(f"{i}/{len(photos)} {ms} мс → {choice}", flush=True)
    print("готово:", out)


if __name__ == "__main__":
    main()
