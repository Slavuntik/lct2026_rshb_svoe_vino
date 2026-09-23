"""Кроп ИМЕННО этикетки по боксам детектора текста (RapidOCR), без отдельной модели-детектора.

Идея (Вячеслав, 22.09): вырезать этикетку, чтобы её текст был крупным уже при 448–640 px —
тогда маленькая VLM и OCR читают её дешевле. Детектор текста OCR уже работает в конвейере (~0.2 с):
боксы текста центральной бутылки = область этикетки.

Эвристика: центральный кроп бутылки (как у VLM) → детекция текста при 960 px → берём боксы в
центральной колонке кропа, начиная с ближайшего к центру, наращиваем кластер по вертикальной
близости → объединяющий прямоугольник + поля → вырезаем ИЗ ОРИГИНАЛА (полное разрешение).
Выход: label-crops/<NNN>.jpg (длинная сторона ≤ 1600) и label-crops/boxes.json.
"""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
F = BASE / "real-photos-labels" / "features"
OUT = BASE / "real-photos-labels" / "label-crops"
SRC = BASE / "real-photos"
CW = (0.15, 0.05, 0.85, 0.98)
DET = 960


def label_bbox(boxes, scores, w, h):
    """Прямоугольник этикетки в координатах кропа (w×h) или None."""
    cand = []
    for b, s in zip(boxes, scores):
        b = np.asarray(b, dtype=np.float32)
        x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()
        cx = (x0 + x1) / 2
        if s < 0.5 or not (0.18 * w <= cx <= 0.82 * w) or (y1 - y0) < 0.008 * h:
            continue
        cand.append([x0, y0, x1, y1])
    if not cand:
        return None
    cand = np.array(cand)
    centers = np.stack([(cand[:, 0] + cand[:, 2]) / 2, (cand[:, 1] + cand[:, 3]) / 2], axis=1)
    # старт — крупный бокс ближе к центру кадра (этикетка обычно в нижней половине бутылки)
    area = (cand[:, 2] - cand[:, 0]) * (cand[:, 3] - cand[:, 1])
    dist = np.hypot((centers[:, 0] - 0.5 * w) / w, (centers[:, 1] - 0.58 * h) / h)
    seed = int(np.argmax(area / area.max() - 1.5 * dist))
    inside = {seed}
    med_h = float(np.median(cand[:, 3] - cand[:, 1]))
    changed = True
    while changed:
        changed = False
        bx0, by0, bx1, by1 = cand[list(inside), 0].min(), cand[list(inside), 1].min(), cand[list(inside), 2].max(), cand[list(inside), 3].max()
        for i in range(len(cand)):
            if i in inside:
                continue
            gap_y = max(cand[i, 1] - by1, by0 - cand[i, 3], 0)
            overlap_x = min(cand[i, 2], bx1 + 0.1 * w) - max(cand[i, 0], bx0 - 0.1 * w)
            if gap_y <= 6.0 * med_h and overlap_x > 0:  # текст на этикетке разрежен: шапка — пробел — название
                inside.add(i); changed = True
    idx = list(inside)
    x0, y0, x1, y1 = cand[idx, 0].min(), cand[idx, 1].min(), cand[idx, 2].max(), cand[idx, 3].max()
    mx, my = 0.18 * (x1 - x0) + 0.02 * w, 0.15 * (y1 - y0) + 0.02 * h
    x0, y0, x1, y1 = x0 - mx, y0 - my, x1 + mx, y1 + my
    # минимальный размер области: этикетка не уже ~45% кропа бутылки и не ниже ~32% его высоты;
    # добираем симметрично по ширине и со смещением ВНИЗ по высоте (шапка с винодельней — вверху этикетки)
    min_w, min_h = 0.45 * w, 0.32 * h
    if x1 - x0 < min_w:
        c = (x0 + x1) / 2; x0, x1 = c - min_w / 2, c + min_w / 2
    if y1 - y0 < min_h:
        add = min_h - (y1 - y0); y0, y1 = y0 - 0.3 * add, y1 + 0.7 * add
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    eng = RapidOCR(params={"Global.use_cls": False, "Det.limit_side_len": DET, "Det.limit_type": "max",
                           "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
                           "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5,
                           "Rec.model_type": ModelType.MOBILE})
    photos = json.loads((F / "photos.json").read_text())
    meta = {}
    for n, name in enumerate(photos, 1):
        im = ImageOps.exif_transpose(Image.open(SRC / name)).convert("RGB")
        W, H = im.size
        cx0, cy0, cx1, cy1 = int(W * CW[0]), int(H * CW[1]), int(W * CW[2]), int(H * CW[3])
        crop = im.crop((cx0, cy0, cx1, cy1))
        small = crop.copy(); small.thumbnail((DET, DET))
        r = eng(np.asarray(small))
        bb = label_bbox(r.boxes, r.scores, *small.size) if r is not None and r.boxes is not None and len(r.boxes) else None
        if bb is None:
            box = (cx0, cy0, cx1, cy1); found = False
        else:
            k = crop.size[0] / small.size[0]
            box = (int(cx0 + bb[0] * k), int(cy0 + bb[1] * k), int(cx0 + bb[2] * k), int(cy0 + bb[3] * k)); found = True
        lab = im.crop(box); lab.thumbnail((1600, 1600))
        lab.save(OUT / f"{n:03d}.jpg", quality=92)
        meta[name] = {"n": n, "box": box, "found": found, "frac": round((box[2] - box[0]) * (box[3] - box[1]) / (W * H), 3)}
    (OUT / "boxes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=0))
    fr = [m["frac"] for m in meta.values() if m["found"]]
    print(f"этикетка найдена на {sum(m['found'] for m in meta.values())}/100; доля кадра: медиана {np.median(fr):.2f}, мин {min(fr):.2f}, макс {max(fr):.2f}")


if __name__ == "__main__":
    main()
