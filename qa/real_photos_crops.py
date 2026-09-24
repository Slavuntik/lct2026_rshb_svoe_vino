"""Близости реальных фото к каталогу для центральных кропов (офлайн, вне git-данные).

Читает features/index_vectors.npy + index_meta.json (выгрузка индекса), пишет
features/sims_<кроп>_<norm|raw>.npy в порядке features/slugs.json.
Запуск из packages/cv: .venv/bin/python ../../qa/real_photos_crops.py
"""
import json
from pathlib import Path

import numpy as np

from cv import imageio
from cv.encoder import SiglipEncoder
from cv.normalize import normalize_query

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
CROPS = {  # доли ширины/высоты: x0, y0, x1, y1
    "cwide": (0.15, 0.05, 0.85, 0.98),
    "cmid": (0.25, 0.20, 0.75, 0.90),
    "ctight": (0.30, 0.35, 0.70, 0.85),
}

V = np.load(F / "index_vectors.npy")
meta = json.loads((F / "index_meta.json").read_text())
slugs = json.loads((F / "slugs.json").read_text())
pos = {s: i for i, s in enumerate(slugs)}
vsi = np.array([pos[s] for s in meta["slugs"]])
photos = json.loads((F / "photos.json").read_text())
enc = SiglipEncoder()


def slug_max(q):
    q = np.asarray(q, dtype=np.float32)
    q /= np.linalg.norm(q)
    out = np.full(len(slugs), -1.0, dtype=np.float32)
    np.maximum.at(out, vsi, V @ q)
    return out


res = {f"{c}_{m}": [] for c in CROPS for m in ("norm", "raw")}
for i, name in enumerate(photos, 1):
    arr = imageio.decode_image((SRC / name).read_bytes())
    h, w = arr.shape[:2]
    for c, (x0, y0, x1, y1) in CROPS.items():
        crop = np.ascontiguousarray(arr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)])
        res[f"{c}_raw"].append(slug_max(enc.encode(crop)))
        res[f"{c}_norm"].append(slug_max(enc.encode(normalize_query(crop, enabled=True))))
    if i % 20 == 0:
        print(f"{i}/{len(photos)}", flush=True)
for k, v in res.items():
    np.save(F / f"sims_{k}.npy", np.stack(v))
print("готово:", list(res))
