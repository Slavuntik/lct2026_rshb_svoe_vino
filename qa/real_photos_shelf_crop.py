"""Локализация бутылки(-ок) на кадре ЦЕЛОЙ ПОЛКИ — без новой модели (задача тимлида 22.09 +
уточнение Вячеслава). Проблема: боевой центральный кроп кадра (доли 0.15-0.85 ширины,
`CWIDE` в `real_photos_rapidocr.py`) на фото организаторов = одна бутылка, но на фото полки
(`Field/`) в него попадают 3-4 бутылки — OCR/CV конвейер получает мешанину текста и проигрывает
(qa-auto: 1/8 top-1, `reports/qa-auto-field-photos.md`).

Идея: боксы детектора текста RapidOCR (уже часть конвейера, ~0.2-0.5 с) = проекция бутылок.
Шаг 0 (сегментация, эта задача):
  1. РЯД (полка): растим окно от геометрического Y-центра кадра наружу по СИЛЬНО сглаженной
     плотности текстовых боксов (`expand_from_center`) — находим ближайшие структурные разрывы
     (полки) по обе стороны. Надёжнее жёсткого разбиения на N рядов порогом (гоняет весь бюджет
     сегментов на шум ценников, см. reports/ml-lead-shelf-crop.md).
  2. КОЛОНКИ (бутылки) внутри выбранного ряда: кластеризация текстовых боксов по X через
     реальные разрывы нулевого покрытия (`run_clusters`); если колонок < 2 — геометрический
     фолбэк (вертикальная проекция границ через Sobel/OpenCV, БЕЗ моделей, `geometric_columns`).
  3. Раздел Вороного между соседними колонками = кроп «одна бутылка с полями»; ближайшая к
     X-центру кадра колонка = «центральная» (метрика); весь список слева направо — для продукта.
  4. ГЕЙТ «это вообще полка» (важно — иначе п.1-3 ложно режут студийные фото каталога, см.
     отчёт, раздел «Гейт»): колонок >= 2 И боксов в полосе ряда >= `SHELF_MIN_BOXES` (порог
     подобран на ВСЕХ 100 фото каталога: максимум боксов у не-полочных — 51, против 41-118 у
     8 целевых полевых рядов — пересечения при пороге 30 нет, 0 регрессий на 62/62 фото
     каталога с истиной; без гейта — катастрофа, 95.2%→51.6%, раздел «Гейт» отчёта). Гейт НЕ
     пройден -> кроп = весь исходный кадр (побитово как раньше, регрессия структурно невозможна).

Дальше — стандартный конвейер на выбранном кропе: 2 эмбеддинга CV (raw + normalize_query)
и RapidOCR 640/960 (чувствительный детектор, box_thresh=0.3/unclip=2.0 — hack-v9).

Прототип по образцу qa/real_photos_label_crops.py.

Режимы (запуск из packages/cv; --eval-* требуют CV_MODEL=google/siglip2-base-patch16-384):
  --dump-field                нарезать все 32 фото Field/ -> case-data/real-photos-labels/
                              field-bottles/<F##>_<k>.jpg + boxes.json (вне git)
  --eval-field                top-1 на 8 sure/likely + офлайн-гейт на 22 NONE, кроп vs без кропа
  --eval-organizer [--always] влияние на 62 фото каталога (гейт по умолчанию; --always = гейт
                              выключен, кроп применяется всегда, для сравнения по заданию)
"""
import argparse, csv, json, sys, time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from rapidocr import RapidOCR, LangRec, OCRVersion, ModelType

sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_v2 import TextIndexV2, sugar_of  # noqa: E402
import text_v2  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
FEAT = LAB / "features"
ORG_SRC = BASE / "real-photos"
FIELD_SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/Field")
FIELD_OUT = LAB / "field-bottles"
GREEK = str.maketrans("ΛΓΠΔΦΡΚΤΗΜΟΑΕΒΖΙΝΥΧ", "ЛГПДФРКТНМОАЕВЗИНУХ")
SHELF_MIN_BOXES = int(__import__("os").environ.get("SHELF_MIN_BOXES", 30))  # см. докстринг, "Гейт"

_DET = None


def det_engine():
    global _DET
    if _DET is None:
        _DET = RapidOCR(params={
            "Global.use_cls": False, "Global.use_rec": False,
            "Det.limit_side_len": 1800, "Det.limit_type": "max",
            "Det.box_thresh": 0.3, "Det.unclip_ratio": 2.0,
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
        })
    return _DET


def rec_engine(size):
    return RapidOCR(params={
        "Global.use_cls": False, "Det.limit_side_len": size, "Det.limit_type": "max",
        "Det.box_thresh": 0.3, "Det.unclip_ratio": 2.0,
        "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
        "Rec.lang_type": LangRec.ESLAV, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
    })


def detect_boxes(im):
    r = det_engine()(np.asarray(im))
    if r is None or r.boxes is None or not len(r.boxes):
        return np.zeros((0, 4, 2), dtype=np.float32)
    return np.asarray(r.boxes, dtype=np.float32)


def intervals_xy(boxes):
    x0 = boxes[:, :, 0].min(axis=1); x1 = boxes[:, :, 0].max(axis=1)
    y0 = boxes[:, :, 1].min(axis=1); y1 = boxes[:, :, 1].max(axis=1)
    return x0, y0, x1, y1


def expand_from_center(a0, a1, frame_size, search_frac=0.4, bins=500, smooth_frac=0.045):
    """Граница «своего» ряда вокруг геометрического центра оси: широко сглаженный профиль
    плотности текстовых боксов -> самая слабая точка (минимум) по каждую сторону от центра
    в пределах `search_frac*frame_size`. Широкое сглаживание гасит мелкие внутристрочные
    прогалы (этикетка/ценник) и оставляет структурные разрывы между полками (см. отчёт)."""
    n = len(a0)
    bin_w = frame_size / bins
    cov = np.zeros(bins, dtype=np.float32)
    for i in range(n):
        i0 = max(0, int(a0[i] / bin_w)); i1 = min(bins, int(np.ceil(a1[i] / bin_w)))
        if i1 > i0:
            cov[i0:i1] += 1
    k = max(3, int(smooth_frac * bins))
    smooth = np.convolve(cov, np.ones(k, dtype=np.float32) / k, mode="same")
    c = int(np.clip(0.5 * bins, 0, bins - 1))
    win = max(1, int(search_frac * bins))
    top_lo = max(0, c - win)
    top = top_lo + int(np.argmin(smooth[top_lo:c])) if c > top_lo else 0
    bot_hi = min(bins, c + win)
    bot = c + int(np.argmin(smooth[c:bot_hi])) if bot_hi > c else bins
    return top * bin_w, bot * bin_w


def run_clusters(a0, a1, frame_size, min_gap_frac=0.02, bins=600):
    """Кластеры по реальным разрывам НУЛЕВОГО покрытия (строже мягкого порога — не режет
    ряд там, где текст просто РЕЖЕ, только там, где боксов нет вовсе)."""
    n = len(a0)
    if n == 0:
        return []
    bin_w = frame_size / bins
    cov = np.zeros(bins, dtype=np.float32)
    for i in range(n):
        i0 = max(0, int(a0[i] / bin_w)); i1 = min(bins, int(np.ceil(a1[i] / bin_w)))
        if i1 > i0:
            cov[i0:i1] += 1
    covered = cov > 0
    runs, i = [], 0
    while i < bins:
        if covered[i]:
            j = i
            while j < bins and covered[j]:
                j += 1
            runs.append([i, j]); i = j
        else:
            i += 1
    if not runs:
        return []
    min_gap_bins = max(1, int(min_gap_frac * bins))
    merged = [runs[0]]
    for r in runs[1:]:
        if r[0] - merged[-1][1] <= min_gap_bins:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [{"a0": i0 * bin_w, "a1": i1 * bin_w, "center": (i0 + i1) / 2 * bin_w} for i0, i1 in merged]


def geometric_columns(gray, y0, y1, W, max_bottles=8, min_bottle_frac=0.08):
    """Фолбэк без модели: впадины вертикальной проекции границ (Sobel-X) = промежутки
    между бутылками, когда текстовых боксов для кластеризации мало/шумно."""
    band = gray[int(y0):int(y1), :].astype(np.float32)
    if band.shape[0] < 4:
        return []
    energy = np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
    k = max(3, W // 150)
    smooth = np.convolve(energy, np.ones(k, dtype=np.float32) / k, mode="same")
    thresh = np.percentile(smooth, 35)
    min_gap_px = max(4, int(0.015 * W))
    min_bottle_px = int(min_bottle_frac * W)
    cand = sorted((smooth[i], i) for i in range(1, W - 1) if smooth[i] <= thresh)
    chosen = []
    for _, i in cand:
        if all(abs(i - c) >= min_gap_px for c in chosen):
            chosen.append(i)
        if len(chosen) >= max_bottles - 1:
            break
    chosen.sort()
    bounds = [0] + chosen + [W]
    segs = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    segs = [s for s in segs if s[1] - s[0] >= min_bottle_px]
    return segs if len(segs) >= 2 else []


def voronoi_bounds(centers, lo, hi):
    b = [lo] + [(c1 + c2) / 2 for c1, c2 in zip(centers, centers[1:])] + [hi]
    return list(zip(b[:-1], b[1:]))


def segment_shelf(im, gate=True):
    """-> dict(crops=[(x0,y0,x1,y1)...] слева направо, center_index, is_shelf, n_row_boxes,
    col_method). Гейт не пройден (или gate=False принудительно НЕ проверяем и просто нет
    текста) -> ровно один crop = весь кадр (побитово как без сегментации)."""
    W, H = im.size
    boxes = detect_boxes(im)
    if len(boxes) == 0:
        return {"crops": [(0, 0, W, H)], "center_index": 0, "is_shelf": False,
                "n_row_boxes": 0, "col_method": "none", "n_boxes": 0}
    x0, y0, x1, y1 = intervals_xy(boxes)
    ry0, ry1 = expand_from_center(y0, y1, H)
    row_mask = (y1 > ry0) & (y0 < ry1)
    n_row_boxes = int(row_mask.sum())
    rx0, rx1 = x0[row_mask], x1[row_mask]

    cols = run_clusters(rx0, rx1, W)
    col_method = "ocr"
    if len(cols) < 2:
        gray = np.asarray(im.convert("L"))
        segs = geometric_columns(gray, ry0, ry1, W)
        if segs:
            cols = [{"center": (a + b) / 2} for a, b in segs]
            col_method = "geometric"
        else:
            col_method = "none"
    is_shelf = len(cols) >= 2 and n_row_boxes >= SHELF_MIN_BOXES
    if gate and not is_shelf:
        return {"crops": [(0, 0, W, H)], "center_index": 0, "is_shelf": False,
                "n_row_boxes": n_row_boxes, "col_method": col_method, "n_boxes": len(boxes)}
    if len(cols) < 2:
        crops = [(0, int(ry0), W, int(ry1))]
        center_index = 0
    else:
        centers = sorted(c["center"] for c in cols)
        bounds = voronoi_bounds(centers, 0, W)
        crops = [(int(a), int(ry0), int(b), int(ry1)) for a, b in bounds]
        center_index = int(np.argmin([abs(c - 0.5 * W) for c in centers]))
    return {"crops": crops, "center_index": center_index, "is_shelf": is_shelf,
            "n_row_boxes": n_row_boxes, "col_method": col_method, "n_boxes": len(boxes)}


# ---------------------------------------------------------------- OCR/CV на кропе (стандартный конвейер)

def ocr_text(engine, crop_img, score=0.5):
    r = engine(np.asarray(crop_img))
    if r is None or not r.txts:
        return ""
    return " ".join(t for t, s in zip(r.txts, r.scores or []) if s >= score)


def crop_for_ocr(im, box, size):
    c = im.crop(box).convert("RGB")
    c.thumbnail((size, size))
    return c


def encode_crop(enc, im, box):
    from cv.normalize import normalize_query
    arr = np.ascontiguousarray(np.asarray(im.crop(box).convert("RGB")))
    raw = np.asarray(enc.encode(arr), dtype=np.float32)
    norm = np.asarray(enc.encode(np.ascontiguousarray(normalize_query(arr, enabled=True))), dtype=np.float32)
    return raw / np.linalg.norm(raw), norm / np.linalg.norm(norm)


# ---------------------------------------------------------------- каталог/текст (как real_photos_cpu_path.py)

def load_catalog_bits():
    from cv import text_rerank as tr
    cat = tr.load_catalog_text(BASE / "strapi_output0709.csv")
    pn, cg = {}, {}
    with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            pn.setdefault(r["Slug"], []).append(r.get("Название фото") or "")
            cg.setdefault(r["Slug"], r.get("Категория") or "")
    return cat, pn, cg


def build_text_indexes(cat, pn, cg, index_slugs):
    extra_slugs = sorted(set(cat) - set(index_slugs))
    all_slugs = list(index_slugs) + extra_slugs
    ex = {s: {"category": cg.get(s, ""), "sugar": sugar_of(s, pn.get(s, []), cat[s].name)} for s in all_slugs}
    sub = {s: cat[s] for s in all_slugs}
    tv_full = TextIndexV2(sub, fields=("name", "winery", "grape", "category", "sugar"), extra=ex)
    tv_win = TextIndexV2(sub, fields=("winery",), extra=ex)
    orig = text_v2.query_tokens
    text_v2.query_tokens = lambda t: orig(t) | orig(t.translate(GREEK))
    return all_slugs, extra_slugs, tv_full, tv_win


def fuse_and_gate(cv_vec, text, tv_full, tv_win, all_slugs, extra_slugs, fam_by, w=0.3, alpha=0.5):
    """-> (top_slug, cv_top1, gap, confident). `cv_vec` — по индексным слагам (без extra)."""
    pad_val = float(cv_vec.max()) - 0.03
    full = np.concatenate([cv_vec, np.full(len(extra_slugs), pad_val, dtype=np.float32)])
    _, mass = tv_full.scores(text); wrec, _ = tv_win.scores(text)
    mass = np.array(mass, dtype=np.float32); wrec = np.array(wrec, dtype=np.float32)
    rel = mass / mass.max() if mass.max() > 0 else mass
    fused = full + w * rel * np.where(wrec >= 0.5, 1.0, alpha)
    top = int(np.argmax(fused))
    order = np.argsort(-full)  # гейт — на CV-скоре (см. докстринг отчёта, "Гейт"), не на fused
    top_cv = int(order[0])
    ft = fam_by.get(all_slugs[top_cv], all_slugs[top_cv])
    j = next((k for k in order[1:] if fam_by.get(all_slugs[k], all_slugs[k]) != ft), None)
    gap = float(full[top_cv] - full[j]) if j is not None else float("inf")
    cv_top1 = float(full[top_cv])
    confident = cv_top1 >= 0.80 and gap >= 0.03
    return all_slugs[top], cv_top1, (None if j is None else round(gap, 4)), confident, fused


def rank_of(fused, all_slugs, true_slug):
    """1-based ранг истинного слага в итоговом (fused) ранжировании — диагностика "верная
    бутылка была в кропе, но не победила" отдельно от чистого top-1."""
    if true_slug not in all_slugs:
        return None
    order = np.argsort(-fused)
    pos = int(np.where(order == all_slugs.index(true_slug))[0][0])
    return pos + 1


# ---------------------------------------------------------------- режим 1: нарезать Field/ целиком

def cmd_dump_field():
    FIELD_OUT.mkdir(parents=True, exist_ok=True)
    with (LAB / "part3-field.csv").open(newline="", encoding="utf-8") as fh:
        fmap = {r["photo"]: r["n"] for r in csv.DictReader(fh)}
    photos = sorted(FIELD_SRC.glob("*.jpeg"))
    meta = {}
    counts = []
    for p in photos:
        fid = fmap.get(p.name, p.stem)
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        res = segment_shelf(im, gate=True)
        counts.append(len(res["crops"]))
        boxes_out = []
        for k, box in enumerate(res["crops"], 1):
            im.crop(box).save(FIELD_OUT / f"{fid}_{k}.jpg", quality=92)
            boxes_out.append(list(box))
        meta[fid] = {"photo": p.name, "n_bottles": len(res["crops"]), "center_index": res["center_index"] + 1,
                     "is_shelf": res["is_shelf"], "n_row_boxes": res["n_row_boxes"], "col_method": res["col_method"],
                     "boxes": boxes_out, "size": list(im.size)}
        print(f"{fid} {p.name[:40]:40s} bottles={len(res['crops'])} shelf={res['is_shelf']} "
              f"method={res['col_method']} row_boxes={res['n_row_boxes']}")
    (FIELD_OUT / "boxes.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    counts = np.array(counts)
    print(f"\nитого {len(photos)} фото; бутылок/кадр: медиана {np.median(counts):.0f}, "
          f"мин {counts.min()}, макс {counts.max()}, sum {counts.sum()}")
    print(f"полка (гейт пройден): {sum(1 for m in meta.values() if m['is_shelf'])}/{len(meta)}")


# ---------------------------------------------------------------- режим 2: top-1 + гейт на 8/22 полевых

def cmd_eval_field():
    from cv.encoder import SiglipEncoder
    with (LAB / "part3-field.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    target = [r for r in rows if r["confidence"] in ("sure", "likely") and r["true_slug"] != "NONE"]
    none_rows = [r for r in rows if r["true_slug"] == "NONE"]
    print(f"целевых (sure/likely, вино в каталоге): {len(target)}; NONE: {len(none_rows)}")

    V = np.load(FEAT / "index_vectors_base384_d1.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    idx_slugs = json.loads((FEAT / "index_meta_base384_d1.json").read_text())["slugs"]
    slugs = sorted(set(idx_slugs)); pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in idx_slugs])
    cat, pn, cg = load_catalog_bits()
    fam_by = __import__("cv.families", fromlist=["load_family_by_slug"]).load_family_by_slug(BASE / "families.json")
    all_slugs, extra_slugs, tv_full, tv_win = build_text_indexes(cat, pn, cg, slugs)

    enc = SiglipEncoder()
    rec640, rec960 = rec_engine(640), rec_engine(960)

    def cvvec(raw, norm):
        S = np.maximum(raw @ V.T, norm @ V.T)
        out = np.full(len(slugs), -1.0, dtype=np.float32)
        np.maximum.at(out, vsi, S)
        return out

    def eval_rows(rows_, tag):
        out = []
        for r in rows_:
            p = FIELD_SRC / r["photo"]
            im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            W, H = im.size
            full_box = (0, 0, W, H)
            seg = segment_shelf(im, gate=True)
            central = seg["crops"][seg["center_index"]]
            for label, box in (("baseline", full_box), ("shelfcrop", central)):
                raw, norm = encode_crop(enc, im, box)
                text = ocr_text(rec640, crop_for_ocr(im, box, 640)) + " " + ocr_text(rec960, crop_for_ocr(im, box, 960))
                cv_ = cvvec(raw, norm)
                top, cv_top1, gap, conf, fused = fuse_and_gate(cv_, text, tv_full, tv_win, all_slugs, extra_slugs, fam_by)
                out.append({"n": r["n"], "true": r["true_slug"], "label": label, "top": top,
                            "correct": top == r["true_slug"], "cv": round(cv_top1, 3), "gap": gap, "confident": conf,
                            "is_shelf": seg["is_shelf"], "n_bottles": len(seg["crops"]),
                            "true_rank": rank_of(fused, all_slugs, r["true_slug"])})
        return out

    print("\n--- 8 целевых (sure/likely) ---")
    res_t = eval_rows(target, "target")
    for label in ("baseline", "shelfcrop"):
        rs = [r for r in res_t if r["label"] == label]
        ok = sum(r["correct"] for r in rs)
        print(f"{label:10s} top-1 = {ok}/{len(rs)}")
        for r in rs:
            print(f"    {r['n']} true={r['true'][:30]:30s} pred={r['top'][:30]:30s} "
                  f"correct={r['correct']} cv={r['cv']} gap={r['gap']} confident={r['confident']} "
                  f"shelf={r['is_shelf']} bottles={r['n_bottles']} true_rank={r['true_rank']}")

    print("\n--- 22 NONE (риск ложной уверенности) ---")
    res_n = eval_rows(none_rows, "none")
    for label in ("baseline", "shelfcrop"):
        rs = [r for r in res_n if r["label"] == label]
        conf = sum(r["confident"] for r in rs)
        print(f"{label:10s} confident(=ложно уверен, т.к. истина NONE) = {conf}/{len(rs)}")
        if conf:
            for r in rs:
                if r["confident"]:
                    print(f"    ЛОЖНО УВЕРЕН {r['n']} pred={r['top']} cv={r['cv']} gap={r['gap']}")

    (FEAT / "field_shelfcrop_eval.json").write_text(json.dumps(res_t + res_n, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- режим 3: влияние на 62 фото каталога

def cmd_eval_organizer(always: bool):
    from cv.encoder import SiglipEncoder
    photos = json.loads((FEAT / "photos.json").read_text())
    photos_set = set(photos)
    labels = {}
    for part in sorted(LAB.glob("part[12].csv")):
        with part.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["confidence"] in ("sure", "likely") and r["photo"] in photos_set:
                    labels[r["photo"]] = r["true_slug"]
    in_cat = {p: t for p, t in labels.items() if t != "NONE"}
    print(f"62-фото сет: {len(in_cat)} в каталоге (из {len(labels)} размеченных)")

    V = np.load(FEAT / "index_vectors_base384_d1.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    idx_slugs = json.loads((FEAT / "index_meta_base384_d1.json").read_text())["slugs"]
    slugs = sorted(set(idx_slugs)); pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in idx_slugs])
    cat, pn, cg = load_catalog_bits()
    fam_by = __import__("cv.families", fromlist=["load_family_by_slug"]).load_family_by_slug(BASE / "families.json")
    all_slugs, extra_slugs, tv_full, tv_win = build_text_indexes(cat, pn, cg, slugs)

    # старые кэши (боевой центральный кроп = весь кадр без сегментации)
    Q = np.load(FEAT / "qemb_base384.npz")
    old_ocr = {}
    for tag in ("rapidS_640", "rapidS_960"):
        for l in (FEAT / f"ocr_{tag}.jsonl").read_text().splitlines():
            d = json.loads(l); old_ocr.setdefault(d["photo"], []).append(d["text"])
    old_text = {p: " ".join(v) for p, v in old_ocr.items()}

    def cvvec(raw, norm):
        S = np.maximum(raw @ V.T, norm @ V.T)
        out = np.full(len(slugs), -1.0, dtype=np.float32)
        np.maximum.at(out, vsi, S)
        return out

    enc = SiglipEncoder()
    rec640, rec960 = rec_engine(640), rec_engine(960)
    n_recrop = 0
    new_cache = {}
    results = {"baseline": {}, "variant": {}}
    seg_stats = {"shelf": 0, "n_boxes_gate_pass": []}
    for i, p in enumerate(photos, 1):
        raw_o = np.asarray(Q["raw"][i - 1], dtype=np.float32); raw_o /= np.linalg.norm(raw_o)
        norm_o = np.asarray(Q["norm"][i - 1], dtype=np.float32); norm_o /= np.linalg.norm(norm_o)
        cv_old = cvvec(raw_o, norm_o)
        top_old, *_ = fuse_and_gate(cv_old, old_text.get(p, ""), tv_full, tv_win, all_slugs, extra_slugs, fam_by)
        results["baseline"][p] = top_old

        im = ImageOps.exif_transpose(Image.open(ORG_SRC / p)).convert("RGB")
        seg = segment_shelf(im, gate=not always)
        is_new_crop = seg["crops"][seg["center_index"]] != (0, 0, im.size[0], im.size[1])
        if seg["is_shelf"]:
            seg_stats["shelf"] += 1
            seg_stats["n_boxes_gate_pass"].append(seg["n_row_boxes"])
        if not is_new_crop:
            results["variant"][p] = top_old
        else:
            n_recrop += 1
            box = seg["crops"][seg["center_index"]]
            raw, norm = encode_crop(enc, im, box)
            text = ocr_text(rec640, crop_for_ocr(im, box, 640)) + " " + ocr_text(rec960, crop_for_ocr(im, box, 960))
            cv_ = cvvec(raw, norm)
            top, *_ = fuse_and_gate(cv_, text, tv_full, tv_win, all_slugs, extra_slugs, fam_by)
            results["variant"][p] = top
            new_cache[p] = {"box": box, "top": top}
        if i % 20 == 0:
            print(f"{i}/{len(photos)} (перекроплено {n_recrop})", flush=True)

    tag = "always" if always else "gated"
    (FEAT / f"organizer_shelfcrop_{tag}.json").write_text(json.dumps(new_cache, ensure_ascii=False, indent=1))
    ok_base = sum(results["baseline"][p] == t for p, t in in_cat.items())
    ok_var = sum(results["variant"][p] == t for p, t in in_cat.items())
    changed = [(p, results["baseline"][p], results["variant"][p], in_cat[p]) for p in in_cat
               if results["baseline"][p] != results["variant"][p]]
    print(f"\nbaseline (без сегментации): {ok_base}/{len(in_cat)} = {ok_base / len(in_cat):.1%}")
    print(f"variant [{tag}]:            {ok_var}/{len(in_cat)} = {ok_var / len(in_cat):.1%}")
    print(f"перекроплено (кроп != весь кадр): {n_recrop}/{len(photos)}; "
          f"гейт «полка» пройден: {seg_stats['shelf']}/{len(photos)}")
    if seg_stats["n_boxes_gate_pass"]:
        print(f"  боксов в ряду у прошедших гейт: {sorted(seg_stats['n_boxes_gate_pass'])}")
    print(f"изменили ответ на 62 (было != стало): {len(changed)}")
    for p, o, v, t in changed:
        print(f"  {p[:30]:30s} было={o[:28]:28s} стало={v[:28]:28s} true={t[:28]:28s} "
              f"{'FIX' if v == t and o != t else ('BREAK' if o == t and v != t else '~')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-field", action="store_true")
    ap.add_argument("--eval-field", action="store_true")
    ap.add_argument("--eval-organizer", action="store_true")
    ap.add_argument("--always", action="store_true", help="--eval-organizer: гейт выключен, кроп всегда")
    a = ap.parse_args()
    t0 = time.time()
    if a.dump_field:
        cmd_dump_field()
    if a.eval_field:
        cmd_eval_field()
    if a.eval_organizer:
        cmd_eval_organizer(a.always)
    print(f"[{time.time() - t0:.1f}s]", file=sys.stderr)
