"""Списки кандидатов слияния CV+текст (v3) для реальных фото + полнота recall@K.

Пишет features/fused_top<K>_<kind>.json: {photo: [{slug, score, name, winery, category, sugar}]}
— вход для VLM-выбора среди кандидатов (vlm-lab/choose.py).
Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_fused_topk.py --kind b384_maxall --w 0.1
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np

from cv import text_rerank as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_v2 import TextIndexV2, sugar_of  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
FEAT = LAB / "features"

ap = argparse.ArgumentParser()
ap.add_argument("--kind", default="b384_maxall")
ap.add_argument("--w", type=float, default=0.1)
ap.add_argument("--ocr", default="crop320")
ap.add_argument("--k", type=int, default=20)
ap.add_argument("--pad", default="p99", choices=["p99", "top-0.03"])
ap.add_argument("--show-miss", action="store_true")
a = ap.parse_args()

photos = json.loads((FEAT / "photos.json").read_text())
slugs = json.loads((FEAT / "slugs.json").read_text())
sims = {f.stem[5:]: np.load(f) for f in FEAT.glob("sims_*.npy")}
s384 = {f.stem[8:]: np.load(f) for f in FEAT.glob("sims384_*.npy")}
KINDS = {
    "max": lambda: np.maximum(sims["norm"], sims["raw"]),
    "b384_max": lambda: np.maximum(s384["norm"], s384["raw"]),
    "b384_maxall": lambda: np.stack(list(s384.values())).max(axis=0),
    "b384_meanall": lambda: np.stack(list(s384.values())).mean(axis=0),
}
CV = KINDS[a.kind]()
cat = tr.load_catalog_text(BASE / "strapi_output0709.csv")
extra = sorted(set(cat) - set(slugs))
all_slugs = slugs + extra
pn, cg = {}, {}
with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
    for r in csv.DictReader(fh):
        pn.setdefault(r["Slug"], []).append(r.get("Название фото") or "")
        cg.setdefault(r["Slug"], r.get("Категория") or "")
ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
tv = TextIndexV2({s: cat[s] for s in all_slugs}, fields=("name", "winery", "grape", "category", "sugar"), extra=ex)
assert tv.slugs == all_slugs
ocr = {json.loads(l)["photo"]: json.loads(l)["text"] for l in (FEAT / f"ocr_{a.ocr}.jsonl").read_text().splitlines() if l.strip()}
labels = {r["photo"]: r["true_slug"] for p in sorted(LAB.glob("part*.csv"))
          for r in csv.DictReader(p.open(newline="", encoding="utf-8")) if r["confidence"] in ("sure", "likely")}

out, hits = {}, {k: 0 for k in (1, 3, 5, 10, 20)}
n_in = 0
for i, p in enumerate(photos):
    cvv = CV[i]
    padv = float(np.percentile(cvv, 99)) if a.pad == "p99" else float(cvv.max()) - 0.03
    full = np.concatenate([cvv, np.full(len(extra), padv, dtype=np.float32)])
    _, mass = tv.scores(ocr.get(p, ""))
    mass = np.array(mass, dtype=np.float32)
    rel = mass / mass.max() if mass.max() > 0 else mass
    fused = full + a.w * rel
    order = np.argsort(-fused, kind="stable")[: a.k]
    out[p] = [{"slug": all_slugs[j], "score": round(float(fused[j]), 4), "name": cat[all_slugs[j]].name,
               "winery": cat[all_slugs[j]].winery, "category": ex[all_slugs[j]]["category"],
               "sugar": ex[all_slugs[j]]["sugar"]} for j in order]
    t = labels.get(p)
    if t and t != "NONE":
        n_in += 1
        top = [c["slug"] for c in out[p]]
        for k in hits:
            hits[k] += t in top[:k]
        if a.show_miss and top[0] != t:
            rk = top.index(t) + 1 if t in top else ">" + str(a.k)
            print(f"ПРОМАХ {p[:28]} истина={t[:50]} (ранг {rk}) | top1={top[0][:50]} | текст={ocr.get(p, '')[:120]!r}")
(FEAT / f"fused_top{a.k}_{a.kind}_{a.ocr}.json").write_text(json.dumps(out, ensure_ascii=False))
print(f"{a.kind} w={a.w} ocr={a.ocr}: n={n_in} " + " ".join(f"@{k}={v/n_in:.1%}" for k, v in hits.items()))
