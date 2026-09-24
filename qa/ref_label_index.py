"""Второй индекс «этикетка к этикетке» (идея Вячеслава, 22.09): у каждого эталона каталога вырезаем
этикетку тем же способом, что у запроса (боксы детектора текста RapidOCR), кодируем SigLIP2 base-384.

Выход (вне git): features/ref_label_vectors.npy, ref_label_meta.json (+ образцы кропов ref-label-crops/).
Запуск из packages/cv: CV_MODEL=google/siglip2-base-patch16-384 .venv/bin/python ../../qa/ref_label_index.py
"""
import json, sys, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real_photos_label_crops import label_bbox  # noqa: E402
from cv.encoder import SiglipEncoder  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
UP = BASE / "prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"
F = BASE / "real-photos-labels" / "features"
SAMPLES = BASE / "real-photos-labels" / "ref-label-crops"
DET = 960

refs = json.loads((BASE / "slug_refs.json").read_text())["mapping"]
items = [(s, v["chosen"]) for s, v in refs.items() if isinstance(v, dict) and v.get("usable") is not False and v.get("chosen")]
print("эталонов:", len(items), flush=True)
eng = RapidOCR(params={"Global.use_cls": False, "Det.limit_side_len": DET, "Det.limit_type": "max",
                       "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                       "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE})
enc = SiglipEncoder()
SAMPLES.mkdir(parents=True, exist_ok=True)
vecs, meta, t0 = [], [], time.time()
for n, (slug, fn) in enumerate(items, 1):
    try:
        im = ImageOps.exif_transpose(Image.open(UP / fn)).convert("RGB")
    except Exception as e:  # noqa: BLE001
        print("пропуск", slug, type(e).__name__); continue
    small = im.copy(); small.thumbnail((DET, DET))
    r = eng(np.asarray(small))
    bb = label_bbox(r.boxes, r.scores, *small.size) if r is not None and r.boxes is not None and len(r.boxes) else None
    if bb is None:
        lab, found = im, False
    else:
        k = im.size[0] / small.size[0]
        lab, found = im.crop((int(bb[0] * k), int(bb[1] * k), int(bb[2] * k), int(bb[3] * k))), True
    v = np.asarray(enc.encode(np.ascontiguousarray(np.asarray(lab))), dtype=np.float32)
    vecs.append(v / np.linalg.norm(v)); meta.append({"slug": slug, "found": found})
    if n <= 40 or n % 100 == 0:
        x = lab.copy(); x.thumbnail((320, 320)); x.save(SAMPLES / f"{n:04d}_{slug[:40]}.jpg", quality=85)
    if n % 200 == 0:
        print(f"{n}/{len(items)} за {time.time() - t0:.0f} с", flush=True)
np.save(F / "ref_label_vectors.npy", np.stack(vecs))
(F / "ref_label_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
print(f"готово: {len(vecs)} векторов, этикетка найдена у {sum(m['found'] for m in meta)}")
