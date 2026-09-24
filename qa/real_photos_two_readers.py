"""Две VLM-читалки этикетки (локальная 4B и 27B на GPU-сервере): как их совмещать.

Схемы (CV base-384, текст → cv.text_fusion-подобный скоринг qa/text_v2.py):
  single_A / single_B  — одна модель;
  union                — тексты двух моделей склеены в один запрос;
  mean_rel             — среднее относительных текстовых скоров двух моделей;
  max_gap              — слияние по каждой модели отдельно, берём ответ с большим
                         отрывом лидера (где модель «увереннее»);
  agree_else_B         — совпали → он; нет → ответ основной модели B.
Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_two_readers.py --a vlm --b gwf1024
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np

from cv import families, text_rerank as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_v2 import TextIndexV2, sugar_of  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
FEAT = LAB / "features"
ap = argparse.ArgumentParser()
ap.add_argument("--a", default="vlm")
ap.add_argument("--b", default="gwf1024")
ap.add_argument("--kind", default="b384_max", choices=["b384_max", "b384_maxall"])
ap.add_argument("--w", type=float, default=0.3)
ap.add_argument("--index", default="", help="суффикс выгрузки индекса: features/index_vectors_<X>.npy (+meta); "
                "пусто — старые sims384_*.npy (индекс G6)")
a = ap.parse_args()

photos = json.loads((FEAT / "photos.json").read_text())
if a.index:
    V = np.load(FEAT / f"index_vectors_{a.index}.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    vs = json.loads((FEAT / f"index_meta_{a.index}.json").read_text())["slugs"]
    slugs = sorted(set(vs)); pos = {s: i for i, s in enumerate(slugs)}; vsi = np.array([pos[s] for s in vs])
    Q = np.load(FEAT / "qemb_base384.npz")
    keys = ["norm", "raw"] if a.kind == "b384_max" else list(Q.files)
    CV = np.full((len(photos), len(slugs)), -1.0, dtype=np.float32)
    for k in keys:
        q = Q[k].astype(np.float32); q /= np.linalg.norm(q, axis=1, keepdims=True)
        S = q @ V.T
        for i in range(len(photos)):
            tmp = np.full(len(slugs), -1.0, dtype=np.float32); np.maximum.at(tmp, vsi, S[i]); CV[i] = np.maximum(CV[i], tmp)
else:
    slugs = json.loads((FEAT / "slugs.json").read_text())
    s384 = {f.stem[8:]: np.load(f) for f in FEAT.glob("sims384_*.npy")}
    CV = np.maximum(s384["norm"], s384["raw"]) if a.kind == "b384_max" else np.stack(list(s384.values())).max(axis=0)
cat = tr.load_catalog_text(BASE / "strapi_output0709.csv")
fam = families.load_family_by_slug(BASE / "families.json")
extra = sorted(set(cat) - set(slugs)); all_slugs = slugs + extra
pn, cg = {}, {}
with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
    for r in csv.DictReader(fh):
        pn.setdefault(r["Slug"], []).append(r.get("Название фото") or ""); cg.setdefault(r["Slug"], r.get("Категория") or "")
ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
tv = TextIndexV2({s: cat[s] for s in all_slugs}, fields=("name", "winery", "grape", "category", "sugar"), extra=ex)
texts = {v: {json.loads(l)["photo"]: json.loads(l)["text"] for l in (FEAT / f"ocr_{v}.jsonl").read_text().splitlines() if l.strip()}
         for v in (a.a, a.b)}
labels = {r["photo"]: r["true_slug"] for p in sorted(LAB.glob("part*.csv"))
          for r in csv.DictReader(p.open(newline="", encoding="utf-8")) if r["confidence"] in ("sure", "likely")}


def rel(text):
    _, mass = tv.scores(text)
    mass = np.array(mass, dtype=np.float32)
    return mass / mass.max() if mass.max() > 0 else mass


def rank(fused):
    o = np.argsort(-fused, kind="stable")
    top = all_slugs[o[0]]
    ft = fam.get(top, top)
    j = next(k for k in o[1:] if fam.get(all_slugs[k], all_slugs[k]) != ft)
    return top, float(fused[o[0]] - fused[j])


res = {k: [0, 0] for k in ("single_A", "single_B", "union", "mean_rel", "max_gap", "agree_else_B")}
agree_stats = {"agree_ok": 0, "agree_all": 0, "disagree_ok_A": 0, "disagree_ok_B": 0, "disagree_all": 0}
for i, p in enumerate(photos):
    t = labels.get(p)
    if not t or t == "NONE":
        continue
    cvv = CV[i]
    full = np.concatenate([cvv, np.full(len(extra), float(cvv.max()) - 0.03, dtype=np.float32)])
    rA, rB = rel(texts[a.a].get(p, "")), rel(texts[a.b].get(p, ""))
    topA, gapA = rank(full + a.w * rA)
    topB, gapB = rank(full + a.w * rB)
    topU, _ = rank(full + a.w * rel(texts[a.a].get(p, "") + " " + texts[a.b].get(p, "")))
    topM, _ = rank(full + a.w * (rA + rB) / 2)
    picks = {"single_A": topA, "single_B": topB, "union": topU, "mean_rel": topM,
             "max_gap": topA if gapA > gapB else topB, "agree_else_B": topB}
    for k, v in picks.items():
        res[k][0] += v == t
        res[k][1] += 1
    if topA == topB:
        agree_stats["agree_all"] += 1; agree_stats["agree_ok"] += topA == t
    else:
        agree_stats["disagree_all"] += 1; agree_stats["disagree_ok_A"] += topA == t; agree_stats["disagree_ok_B"] += topB == t
print(f"A={a.a} B={a.b} kind={a.kind} w={a.w} index={a.index or 'G6-v6'} слагов в индексе={len(slugs)}")
for k, (ok, n) in res.items():
    print(f"  {k:14s} {ok}/{n} = {ok / n:.1%}")
print("  согласие:", agree_stats)
