"""Тайминги CV (SigLIP2 base-384) и PaddleOCR на ams3 — компоненты CPU-пути слияния."""
import glob, os, statistics as st, sys, time
os.environ["CV_MODEL"] = "google/siglip2-base-patch16-384"
os.environ["HF_HOME"] = "/opt/somelye/data/models/hf"; os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"
sys.path.insert(0, "/opt/somelye/app/packages/cv")
import numpy as np
from PIL import Image
import torch
from cv.encoder import SiglipEncoder
from cv.normalize import normalize_query
from cv.verify import LabelVerifier
print("потоков torch:", torch.get_num_threads(), "| OMP_NUM_THREADS =", os.environ.get("OMP_NUM_THREADS"))
imgs640 = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(glob.glob("/opt/somelye/cpulab/img/*_640.jpg"))]
imgs448 = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(glob.glob("/opt/somelye/cpulab/img/*_448.jpg"))]
enc = SiglipEncoder(); enc.encode(imgs640[0])
t_enc, t_norm = [], []
for a in imgs640:
    t = time.time(); enc.encode(a); t_enc.append(time.time() - t)
    t = time.time(); n = normalize_query(a, enabled=True); t_norm.append(time.time() - t)
print(f"SigLIP-384 encode: медиана {st.median(t_enc)*1000:.0f} мс (макс {max(t_enc)*1000:.0f}); normalize_query: {st.median(t_norm)*1000:.0f} мс")
for size, imgs in ((640, imgs640), (320, imgs448)):
    v = LabelVerifier(ocr_size=size); v.ocr_size = size; v.read_text(imgs[0])
    ts, words = [], 0
    for a in imgs:
        t = time.time(); txt = v.read_text(a); ts.append(time.time() - t); words += len(txt.split())
    print(f"PaddleOCR {size}px: медиана {st.median(ts):.2f} с (макс {max(ts):.2f}), слов прочитано {words}")
