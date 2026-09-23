"""Каскад «сначала индекс этикеток, потом полный цикл» на живых фото (идея Вячеслава, 22.09).

Ступень 1: кроп этикетки запроса → SigLIP → индекс этикеток эталонов (qa/ref_label_index.py).
Ступень 2: полный цикл — CV по бутылке (2 кропа) + текст RapidOCR 640+960 (слияние).
Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_label_cascade.py
"""
import csv, json, sys
from pathlib import Path

import numpy as np

from cv import families, text_rerank as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import text_v2  # noqa: E402
from text_v2 import TextIndexV2, sugar_of  # noqa: E402

text_v2._LOW["i"] = "и"
GREEK = str.maketrans("ΛΓΠΔΦΡΚΤΗΜΟΑΕΒΖΙΝΥΧ", "ЛГПДФРКТНМОАЕВЗИНУХ")
_orig = text_v2.query_tokens
text_v2.query_tokens = lambda t: _orig(t) | _orig(t.translate(GREEK))

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"); LAB = BASE / "real-photos-labels"; F = LAB / "features"
cat = tr.load_catalog_text(BASE / "strapi_output0709.csv"); fam = families.load_family_by_slug(BASE / "families.json")
photos = json.loads((F / "photos.json").read_text())
V = np.load(F / "index_vectors_base384_d1.npy").astype(np.float32); V /= np.linalg.norm(V, axis=1, keepdims=True)
vs = json.loads((F / "index_meta_base384_d1.json").read_text())["slugs"]
slugs = sorted(set(vs)); pos = {s: i for i, s in enumerate(slugs)}; vsi = np.array([pos[s] for s in vs])
Q = np.load(F / "qemb_base384.npz"); QL = np.load(F / "qemb_label_base384.npz")
R = np.load(F / "ref_label_vectors.npy").astype(np.float32); rmeta = json.loads((F / "ref_label_meta.json").read_text())
rsi = np.array([pos.get(m["slug"], -1) for m in rmeta])


def per_slug(S, idx):
    out = np.full((S.shape[0], len(slugs)), -1.0, dtype=np.float32)
    ok = idx >= 0
    for i in range(S.shape[0]):
        np.maximum.at(out[i], idx[ok], S[i][ok])
    return out


def unit(a):
    a = a.astype(np.float32); return a / np.linalg.norm(a, axis=1, keepdims=True)


CVB = np.maximum(per_slug(unit(Q["norm"]) @ V.T, vsi), per_slug(unit(Q["raw"]) @ V.T, vsi))      # бутылка, 2 кропа
CVL = np.maximum(per_slug(unit(QL["label_raw"]) @ R.T, rsi), per_slug(unit(QL["label_norm"]) @ R.T, rsi))  # этикетка→этикетка
extra = sorted(set(cat) - set(slugs)); all_slugs = slugs + extra
pn, cg = {}, {}
with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
    for r in csv.DictReader(fh):
        pn.setdefault(r["Slug"], []).append(r.get("Название фото") or ""); cg.setdefault(r["Slug"], r.get("Категория") or "")
ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
sub = {s: cat[s] for s in all_slugs}
tv = TextIndexV2(sub, fields=("name", "winery", "grape", "category", "sugar"), extra=ex); tw = TextIndexV2(sub, fields=("winery",), extra=ex)
texts = {}
for v in ("rapid_640", "rapid_960"):
    for l in (F / f"ocr_{v}.jsonl").read_text().splitlines():
        d = json.loads(l); texts[d["photo"]] = texts.get(d["photo"], "") + " " + d["text"]
labels = {r["photo"]: r["true_slug"] for p in sorted(LAB.glob("part*.csv")) for r in csv.DictReader(p.open()) if r["confidence"] in ("sure", "likely")}
items = [(photos.index(p), t) for p, t in labels.items() if t != "NONE"]
none_items = [photos.index(p) for p, t in labels.items() if t == "NONE"]


def pad(v):
    return np.concatenate([v, np.full(len(extra), float(v.max()) - 0.03, dtype=np.float32)])


def text_rel(i):
    mass, _m = tv.scores(texts.get(photos[i], "")); mass = np.array(_m, dtype=np.float32)
    wrec, _ = tw.scores(texts.get(photos[i], ""))
    rel = mass / mass.max() if mass.max() > 0 else mass
    return rel * np.where(np.array(wrec) >= 0.5, 1.0, 0.5)


REL = {i: text_rel(i) for i, _ in items}
REL.update({i: text_rel(i) for i in none_items})


def top_and_gap(vec):
    o = np.argsort(-vec); top = o[0]; ft = fam.get(all_slugs[top], all_slugs[top]) if top < len(all_slugs) else None
    j = next(k for k in o[1:] if fam.get(all_slugs[k], all_slugs[k]) != ft)
    return int(top), float(vec[top] - vec[j]), float(vec[top])


def acc(fn):
    return sum(all_slugs[fn(i)] == t for i, t in items)


n = len(items)
print(f"фото из каталога: {n}")
print(f"1. только индекс этикеток:        {acc(lambda i: int(np.argmax(CVL[i])))}/{n}")
print(f"2. только CV по бутылке (2 кропа): {acc(lambda i: int(np.argmax(CVB[i])))}/{n}")
print(f"3. полный цикл (CV бутылки + OCR): {acc(lambda i: top_and_gap(pad(CVB[i]) + 0.3 * REL[i])[0])}/{n}")
for lam in (0.5, 1.0):
    print(f"4. всё вместе: max(бутылка, {lam}·этикетка) + OCR: {acc(lambda i: top_and_gap(pad(np.maximum(CVB[i], lam * CVL[i] + (1 - lam) * CVB[i])) + 0.3 * REL[i])[0])}/{n}")
print(f"5. всё вместе: среднее(бутылка, этикетка) + OCR: {acc(lambda i: top_and_gap(pad((CVB[i] + CVL[i]) / 2) + 0.3 * REL[i])[0])}/{n}")
print("\nкаскад: ступень 1 отвечает сама, если скор этикетки ≥ T и отрыв ≥ G; иначе полный цикл")
print(f"{'T':>5s} {'G':>5s} | отвечено ступенью 1 | из них верно | итог каскада | NONE-фото, «уверенно» отвеченные ступенью 1")
for T in (0.80, 0.85, 0.88, 0.90):
    for G in (0.02, 0.04, 0.06):
        s1 = ok1 = total = 0
        for i, t in items:
            top, gap, sc = top_and_gap(pad(CVL[i]))
            if sc >= T and gap >= G:
                s1 += 1; ok1 += all_slugs[top] == t; total += all_slugs[top] == t
            else:
                total += all_slugs[top_and_gap(pad(CVB[i]) + 0.3 * REL[i])[0]] == t
        nn = sum(1 for i in none_items if (lambda r: r[2] >= T and r[1] >= G)(top_and_gap(pad(CVL[i]))))
        print(f"{T:5.2f} {G:5.2f} | {s1:3d}/{n} ({s1 / n:4.0%})        | {ok1:3d}/{s1:<3d}      | {total}/{n} = {total / n:.1%} | {nn}/{len(none_items)}")
agree = [(i, t) for i, t in items if int(np.argmax(CVL[i])) == top_and_gap(pad(CVB[i]) + 0.3 * REL[i])[0]]
print(f"\nсогласие ступеней (top-1 совпал): {len(agree)}/{n}, из них верно {sum(all_slugs[int(np.argmax(CVL[i]))] == t for i, t in agree)}")
