"""Кривая «сколько ракурсов в индексе → top-1» на РЕАЛЬНЫХ размеченных фото (офлайн).

Берёт выгрузку боевого индекса (features/index_vectors.npy + index_meta.json) и эмбеддинги
запросов (features/qemb_<tag>.npz), считает top-1/top-5 для подмножеств ракурсов:
только эталон(ы), эталон + synth-1..k. Запуск из packages/cv:
  .venv/bin/python ../../qa/real_photos_views.py --tag base224
"""
import argparse, csv, json
from pathlib import Path

import numpy as np

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
LAB = F.parent

ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="base224")
ap.add_argument("--conf", default="sure,likely")
# G6 (agents/G6-bigger-encoder.md, доп. задание оркестратора 21.09): сравнение энкодеров-
# кандидатов на реальных фото — своя выгрузка qdrant (qa/g6_dump_qdrant_vectors.py) вместо
# боевой index_vectors.npy/index_meta.json. Default не меняется (обратная совместимость).
ap.add_argument("--vectors", default=None, help="кастомная выгрузка векторов индекса (default: боевой index_vectors.npy)")
ap.add_argument("--meta", default=None, help="кастомные метаданные индекса (default: боевой index_meta.json)")
a = ap.parse_args()

V = np.load(Path(a.vectors) if a.vectors else (F / "index_vectors.npy"))
meta = json.loads((Path(a.meta) if a.meta else (F / "index_meta.json")).read_text())
vslugs = np.array(meta["slugs"])
views = np.array(meta["views"])
uniq = sorted(set(meta["slugs"]))
pos = {s: i for i, s in enumerate(uniq)}
vsi = np.array([pos[s] for s in vslugs])
Q = np.load(F / f"qemb_{a.tag}.npz")
photos = json.loads((F / "photos.json").read_text())
confs = set(a.conf.split(","))
labels = {}
for p in sorted(LAB.glob("part*.csv")):
    for r in csv.DictReader(p.open(newline="", encoding="utf-8")):
        if r["confidence"] in confs and r["true_slug"] not in ("", "NONE"):
            labels[r["photo"]] = r["true_slug"]
items = [(photos.index(p), t) for p, t in labels.items() if t in pos]
print(f"фото с истиной в индексе: {len(items)} (всего размечено в каталоге: {len(labels)})")

is_real = np.char.startswith(views.astype(str), "real")
synth_n = np.array([int(v.split("-")[1]) if v.startswith("synth-") else 0 for v in views])


def evaluate(mask, kinds):
    Vm, sm = V[mask], vsi[mask]
    t1 = t5 = 0
    for i, truth in items:
        best = np.full(len(uniq), -1.0, dtype=np.float32)
        for k in kinds:
            np.maximum.at(best, sm, Vm @ Q[k][i])
        order = np.argsort(-best)[:5]
        top = [uniq[j] for j in order]
        t1 += top[0] == truth
        t5 += truth in top
    n = len(items)
    return t1 / n, t5 / n


subsets = [("только эталоны", is_real)] + [(f"эталон + synth 1..{k}", is_real | ((synth_n >= 1) & (synth_n <= k))) for k in (3, 6, 12, 18, 24)] + [("только synth 1..24", synth_n >= 1)]
kindsets = {"norm": ["norm"], "norm+raw": ["norm", "raw"], "все кропы": [k for k in Q.files]}
print(f"{'подмножество':24s} {'векторов':>9s} " + " ".join(f"{k:>18s}" for k in kindsets))
for name, mask in subsets:
    cells = []
    for kn, ks in kindsets.items():
        t1, t5 = evaluate(mask, ks)
        cells.append(f"{t1:7.1%} / {t5:6.1%}  ")
    print(f"{name:24s} {int(mask.sum()):9d} " + " ".join(f"{c:>18s}" for c in cells))
