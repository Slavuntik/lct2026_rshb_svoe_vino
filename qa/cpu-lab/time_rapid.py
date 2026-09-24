"""RapidOCR (ONNX Runtime) на кропах этикетки 640 px: время и текст, детектор mobile/server."""
import glob, statistics as st, time
import numpy as np
from PIL import Image
from rapidocr import RapidOCR, LangRec, LangDet, OCRVersion, ModelType
imgs = [(p.split("/")[-1], np.asarray(Image.open(p).convert("RGB"))) for p in sorted(glob.glob("/opt/somelye/cpulab/img/*_640.jpg"))]
for det_type in (ModelType.MOBILE, ModelType.SERVER):
    try:
        eng = RapidOCR(params={"Global.use_cls": False,
                               "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": det_type,
                               "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE})
    except Exception as e:  # noqa: BLE001
        print("det", det_type.name, "не поднялся:", type(e).__name__, str(e)[:150]); continue
    eng(imgs[0][1])
    ts, words = [], 0
    for name, a in imgs:
        t = time.time(); r = eng(a); ts.append(time.time() - t)
        txts = [x for x, s in zip(r.txts or [], r.scores or []) if s >= 0.5] if r is not None and r.txts else []
        words += sum(len(x.split()) for x in txts)
        print(f"  [{det_type.name}] {name}: {ts[-1]:.2f} с | {' '.join(txts)[:110]}")
    print(f"RapidOCR det={det_type.name}: медиана {st.median(ts):.2f} с (макс {max(ts):.2f}), слов {words}")
