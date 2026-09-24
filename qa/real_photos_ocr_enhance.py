"""Варианты предобработки кропа этикетки перед OCR (мелкий низкоконтрастный текст):
clahe — выравнивание контраста по L-каналу; sharp — нерезкое маскирование; halves — верхняя и
нижняя половины кропа этикетки каждая при <size> px (вдвое больше пикселей на строку); gray_inv —
серый + инверсия (золото по тёмному). Выход: features/ocr_lab_<variant>_<size>.jsonl.
"""
import argparse, json, time
from pathlib import Path
import cv2, numpy as np
from PIL import Image, ImageFilter
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType
F = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/features")
LC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/label-crops")
ap = argparse.ArgumentParser(); ap.add_argument("--variants", default="clahe,sharp,halves,gray_inv"); ap.add_argument("--size", type=int, default=1280); a = ap.parse_args()
photos = json.loads((F / "photos.json").read_text())
eng = RapidOCR(params={"Global.use_cls": False, "Det.limit_side_len": a.size, "Det.limit_type": "max", "Det.ocr_version": OCRVersion.PPOCRV5,
                       "Det.model_type": ModelType.MOBILE, "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE})
def clahe(im):
    lab = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2LAB); l, aa, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return Image.fromarray(cv2.cvtColor(cv2.merge((l, aa, b)), cv2.COLOR_LAB2RGB))
def sharp(im): return im.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
def gray_inv(im): return Image.fromarray(255 - np.asarray(im.convert("L")))
def ocr(im):
    x = im.copy(); x.thumbnail((a.size, a.size)); r = eng(np.asarray(x.convert("RGB")), use_det=True, use_cls=False, use_rec=True)
    return [t for t, s in zip(r.txts or [], r.scores or []) if s >= 0.5] if r is not None and r.txts else []
for var in a.variants.split(","):
    out = F / f"ocr_lab_{var}_{a.size}.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for n, name in enumerate(photos, 1):
            im = Image.open(LC / f"{n:03d}.jpg").convert("RGB"); t0 = time.time()
            if var == "halves":
                w, h = im.size; txt = ocr(im.crop((0, 0, w, int(h * 0.55)))) + ocr(im.crop((0, int(h * 0.45), w, h)))
            else:
                txt = ocr({"clahe": clahe, "sharp": sharp, "gray_inv": gray_inv}[var](im))
            fh.write(json.dumps({"photo": name, "text": " ".join(txt), "ms": round((time.time() - t0) * 1000)}, ensure_ascii=False) + "\n")
    print("готово:", out)
