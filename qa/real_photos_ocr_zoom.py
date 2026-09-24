"""Второй проход OCR «зум по боксам» (идея Вячеслава, 22.09): детектор при 960 px даёт боксы
строк; каждая строка вырезается ИЗ ОРИГИНАЛА (полное разрешение) и распознаётся отдельно —
мелкие строки при необходимости увеличиваются бикубически до рабочей высоты распознавателя.
Выход: features/ocr_zoom[_nobicubic].jsonl (только текст второго прохода) + время.

Запуск: python qa/real_photos_ocr_zoom.py [--min-h 48] [--no-bicubic] [--tag zoom]
"""
import argparse, json, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos")
CW = (0.15, 0.05, 0.85, 0.98)
ap = argparse.ArgumentParser()
ap.add_argument("--det", type=int, default=960)
ap.add_argument("--min-h", type=int, default=48, help="целевая высота строки для распознавателя")
ap.add_argument("--no-bicubic", action="store_true", help="не увеличивать мелкие строки (как есть)")
ap.add_argument("--tag", default="zoom")
ap.add_argument("--score", type=float, default=0.5)
a = ap.parse_args()
eng = RapidOCR(params={"Global.use_cls": False, "Det.limit_side_len": a.det, "Det.limit_type": "max",
                       "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                       "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE})
photos = json.loads((F / "photos.json").read_text())
out = F / f"ocr_{a.tag}.jsonl"
with out.open("w", encoding="utf-8") as fh:
    for name in photos:
        im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB"); W, H = im.size
        crop = im.crop((int(W * CW[0]), int(H * CW[1]), int(W * CW[2]), int(H * CW[3])))
        small = crop.copy(); small.thumbnail((a.det, a.det)); k = crop.size[0] / small.size[0]
        t0 = time.time()
        r = eng(np.asarray(small), use_det=True, use_cls=False, use_rec=True)
        t_det = time.time() - t0
        texts, n_small, n_boxes = [], 0, 0
        t1 = time.time()
        if r is not None and r.boxes is not None:
            for b in r.boxes:
                b = np.asarray(b, dtype=np.float32) * k
                x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()
                h = y1 - y0; mx, my = 0.08 * (x1 - x0) + 4, 0.25 * h + 4
                line = crop.crop((max(0, x0 - mx), max(0, y0 - my), min(crop.size[0], x1 + mx), min(crop.size[1], y1 + my)))
                n_boxes += 1
                if line.size[1] < a.min_h:
                    n_small += 1
                    if not a.no_bicubic:
                        f = a.min_h / line.size[1]
                        line = line.resize((max(8, int(line.size[0] * f)), a.min_h), Image.BICUBIC)
                rr = eng(np.asarray(line), use_det=False, use_cls=False, use_rec=True)
                if rr is not None and rr.txts:
                    for tx, sc in zip(rr.txts, rr.scores or [1.0]):
                        if sc >= a.score and tx.strip():
                            texts.append(tx.strip())
        t_zoom = time.time() - t1
        fh.write(json.dumps({"photo": name, "text": " ".join(texts), "ms": round(t_zoom * 1000),
                             "det_ms": round(t_det * 1000), "boxes": n_boxes, "small": n_small}, ensure_ascii=False) + "\n")
print("готово:", out)
