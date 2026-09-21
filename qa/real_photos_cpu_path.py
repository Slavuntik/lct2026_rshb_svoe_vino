"""CPU-путь слияния на живых фото: источник текста × поправки матчинга (греческие двойники OCR,
гейт «винодельня подтверждена») × число кропов CV. Индекс — выгрузка D1 (base-384).

Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_cpu_path.py --ocr rapid_640,rapid_960 [--union rapid_640+rapid_960]
"""
import argparse, csv, json, sys
from pathlib import Path

import numpy as np

from cv import text_rerank as tr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import text_v2  # noqa: E402
from text_v2 import TextIndexV2, sugar_of  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
F = LAB / "features"
GREEK = str.maketrans("ΛΓΠΔΦΡΚΤΗΜΟΑΕΒΖΙΝΥΧ", "ЛГПДФРКТНМОАЕВЗИНУХ")

ap = argparse.ArgumentParser()
ap.add_argument("--ocr", default="crop320,cwide640,rapid_640")
ap.add_argument("--union", default="", help="объединения через запятую, части через +: rapid_640+rapid_960")
ap.add_argument("--w", default="0.3")
ap.add_argument("--alpha", default="1.0,0.5")
ap.add_argument("--show-miss", default="", help="вариант, для которого печатать промахи (при первом W и последнем alpha)")
a = ap.parse_args()

cat = tr.load_catalog_text(BASE / "strapi_output0709.csv")
photos = json.loads((F / "photos.json").read_text())
V = np.load(F / "index_vectors_base384_d1.npy").astype(np.float32); V /= np.linalg.norm(V, axis=1, keepdims=True)
vs = json.loads((F / "index_meta_base384_d1.json").read_text())["slugs"]
slugs = sorted(set(vs)); pos = {s: i for i, s in enumerate(slugs)}; vsi = np.array([pos[s] for s in vs])
Q = np.load(F / "qemb_base384.npz")


def cvmat(keys):
    CV = np.full((len(photos), len(slugs)), -1.0, dtype=np.float32)
    for k in keys:
        q = Q[k].astype(np.float32); q /= np.linalg.norm(q, axis=1, keepdims=True); S = q @ V.T
        for i in range(len(photos)):
            t = np.full(len(slugs), -1.0, dtype=np.float32); np.maximum.at(t, vsi, S[i]); CV[i] = np.maximum(CV[i], t)
    return CV


CVS = {"2 кропа": cvmat(("norm", "raw")), "8 кропов": cvmat(list(Q.files))}
extra = sorted(set(cat) - set(slugs)); all_slugs = slugs + extra
pn, cg = {}, {}
with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
    for r in csv.DictReader(fh):
        pn.setdefault(r["Slug"], []).append(r.get("Название фото") or ""); cg.setdefault(r["Slug"], r.get("Категория") or "")
ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
sub = {s: cat[s] for s in all_slugs}
tv_full = TextIndexV2(sub, fields=("name", "winery", "grape", "category", "sugar"), extra=ex)
tv_win = TextIndexV2(sub, fields=("winery",), extra=ex)
labels = {r["photo"]: r["true_slug"] for p in sorted(LAB.glob("part*.csv"))
          for r in csv.DictReader(p.open(newline="", encoding="utf-8")) if r["confidence"] in ("sure", "likely")}


def load(v):
    return {json.loads(l)["photo"]: json.loads(l)["text"] for l in (F / f"ocr_{v}.jsonl").read_text().splitlines() if l.strip()}


texts = {v: load(v) for v in a.ocr.split(",") if v}
for u in [x for x in a.union.split(",") if x]:
    parts = [load(p) for p in u.split("+")]
    texts[u] = {ph: " ".join(p.get(ph, "") for p in parts) for ph in photos}
orig = text_v2.query_tokens
text_v2.query_tokens = lambda t: orig(t) | orig(t.translate(GREEK))
cache = {}


def scores(variant, p):
    if (variant, p) not in cache:
        text = texts[variant].get(p, "")
        _, mass = tv_full.scores(text); wrec, _ = tv_win.scores(text)
        cache[(variant, p)] = (np.array(mass, dtype=np.float32), np.array(wrec, dtype=np.float32))
    return cache[(variant, p)]


def run(variant, CV, W, alpha):
    ok = n = 0; miss = []
    for p, t in labels.items():
        if t == "NONE":
            continue
        i = photos.index(p); cvv = CV[i]
        full = np.concatenate([cvv, np.full(len(extra), float(cvv.max()) - 0.03, dtype=np.float32)])
        mass, wrec = scores(variant, p)
        rel = mass / mass.max() if mass.max() > 0 else mass
        top = all_slugs[int(np.argmax(full + W * rel * np.where(wrec >= 0.5, 1.0, alpha)))]
        n += 1; ok += top == t
        if top != t:
            miss.append((p[:6], t[:34], top[:34], texts[variant].get(p, "")[:60]))
    return ok, n, miss


Ws = [float(x) for x in a.w.split(",")]; alphas = [float(x) for x in a.alpha.split(",")]
print(f"{'источник текста':28s} {'CV':9s} " + " ".join(f"W={w} α={al}".rjust(14) for w in Ws for al in alphas))
for variant in texts:
    for cvn, CV in CVS.items():
        cells = []
        for w in Ws:
            for al in alphas:
                ok, n, _ = run(variant, CV, w, al); cells.append(f"{ok}/{n} {ok / n:.1%}".rjust(14))
        print(f"{variant:28s} {cvn:9s} " + " ".join(cells))
if a.show_miss:
    ok, n, miss = run(a.show_miss, CVS["2 кропа"], Ws[0], alphas[-1])
    print(f"\nпромахи {a.show_miss} (2 кропа, W={Ws[0]}, α={alphas[-1]}):")
    for m in miss:
        print("  ", m)
