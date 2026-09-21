"""Эмбеддинги запросов реальных фото (вся картинка/нормализация/кропы) — для офлайн-
экспериментов с подмножествами индекса (кривая «ракурсы → top-1» на реальных фото).

Пишет features/qemb_<model-tag>.npz: ключи norm, raw, <кроп>_norm, <кроп>_raw → [N_фото x D].
Запуск из packages/cv: [CV_MODEL=...] .venv/bin/python ../../qa/real_photos_qemb.py --tag base224
"""
import argparse, json
from pathlib import Path

import numpy as np

from cv import imageio
from cv.encoder import SiglipEncoder
from cv.normalize import normalize_query

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
CROPS = {"cwide": (0.15, 0.05, 0.85, 0.98), "cmid": (0.25, 0.20, 0.75, 0.90), "ctight": (0.30, 0.35, 0.70, 0.85)}

ap = argparse.ArgumentParser()
ap.add_argument("--tag", required=True)
a = ap.parse_args()
photos = json.loads((F / "photos.json").read_text())
enc = SiglipEncoder()
out = {k: [] for k in ["norm", "raw"] + [f"{c}_{m}" for c in CROPS for m in ("norm", "raw")]}


def e(x):
    v = np.asarray(enc.encode(np.ascontiguousarray(x)), dtype=np.float32)
    return v / np.linalg.norm(v)


for i, name in enumerate(photos, 1):
    arr = imageio.decode_image((SRC / name).read_bytes())
    h, w = arr.shape[:2]
    out["raw"].append(e(arr))
    out["norm"].append(e(normalize_query(arr, enabled=True)))
    for c, (x0, y0, x1, y1) in CROPS.items():
        crop = arr[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
        out[f"{c}_raw"].append(e(crop))
        out[f"{c}_norm"].append(e(normalize_query(np.ascontiguousarray(crop), enabled=True)))
    if i % 25 == 0:
        print(f"{i}/{len(photos)}", flush=True)
np.savez(F / f"qemb_{a.tag}.npz", **{k: np.stack(v) for k, v in out.items()})
print("готово", F / f"qemb_{a.tag}.npz")
