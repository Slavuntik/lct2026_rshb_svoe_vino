"""RapidOCR (ONNX Runtime, модели PP-OCRv5: детектор mobile + распознаватель eslav) на живых фото →
features/ocr_rapid_<size>.jsonl. Вход — центральный кроп бутылки (как у VLM), длинная сторона <size>.

Запуск: python qa/real_photos_rapidocr.py --sizes 640,960 [--det mobile|server]
"""
import argparse, json, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
ap = argparse.ArgumentParser()
ap.add_argument("--sizes", default="640,960")
ap.add_argument("--det", default="mobile", choices=["mobile", "server"])
ap.add_argument("--score", type=float, default=0.5)
ap.add_argument("--images-dir", default="", help="готовые кропы NNN.jpg (порядок photos.json) вместо центрального кропа кадра")
ap.add_argument("--prefix", default="rapid", help="префикс тега выходного файла")
ap.add_argument("--box-thresh", type=float, default=None, help="Det.box_thresh (дефолт движка 0.6); 0.3 собирает строки вразрядку")
ap.add_argument("--unclip", type=float, default=None, help="Det.unclip_ratio (дефолт движка 1.5)")
a = ap.parse_args()
photos = json.loads((F / "photos.json").read_text())
for size in [int(s) for s in a.sizes.split(",")]:
    det_extra = {k: v for k, v in (("Det.box_thresh", a.box_thresh), ("Det.unclip_ratio", a.unclip)) if v is not None}
    eng = RapidOCR(params={**det_extra, "Global.use_cls": False, "Det.limit_side_len": size, "Det.limit_type": "max",
                           "Det.ocr_version": OCRVersion.PPOCRV5,
                           "Det.model_type": ModelType.MOBILE if a.det == "mobile" else ModelType.SERVER,
                           "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5,
                           "Rec.model_type": ModelType.MOBILE})
    tag = f"{a.prefix}_{size}" + ("" if a.det == "mobile" else "_srv")
    out = F / f"ocr_{tag}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for n, name in enumerate(photos, 1):
            if a.images_dir:
                im = Image.open(Path(a.images_dir) / f"{n:03d}.jpg").convert("RGB")
            else:
                im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB"); w, h = im.size
                im = im.crop((int(w * .15), int(h * .05), int(w * .85), int(h * .98)))
            im.thumbnail((size, size))
            t = time.time(); r = eng(np.asarray(im)); ms = round((time.time() - t) * 1000)
            txts = [x for x, s in zip(r.txts or [], r.scores or []) if s >= a.score] if r is not None and r.txts else []
            fh.write(json.dumps({"photo": name, "text": " ".join(txts), "ms": ms}, ensure_ascii=False) + "\n")
    print("готово:", out)
