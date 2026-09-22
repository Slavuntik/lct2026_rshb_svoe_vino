"""ml-lead: гейт «кадр полки» v2 — офлайн поиск доп. сигнала против ложного срабатывания
гейта v1 (`cv.shelf_crop.segment_boxes`, packages/cv/cv/shelf_crop.py, коммит 8459f57:
колонок >= 2 И боксов в ряду >= 30) на ОДИНОЧНОЙ бутылке со сложной вёрсткой этикетки.

Основание (тимлид, 22.09; полный разбор — reports/ml-eng-ml2.md): живая приёмка ML-2 дала
регрессию 95.2%→93.5% (59/62→58/62) на "87.88_28-08-2026_16-56-20.webp" — гейт v1 ложно
сработал (col_method=ocr, n_row_boxes=37>=30, 2 колонки — оба X-разрыва внутри этикетки
ОДНОЙ бутылки, не между бутылками), кроп срезал ~38% кадра слева, near-dup переключился на
соседа по семье. Блокер ml-engineer: "нужен доп. сигнал (площадь/пропорции центральной
колонки к кадру)".

Задача (ограниченная): найти сигнал+порог, который НИ РАЗУ не срабатывает на ВСЕХ 100 фото
организаторов (case-data/real-photos, 62 в каталоге + 38 NONE), но срабатывает на настоящих
полках (Field/). `cv.shelf_crop` НЕ правится — только импортируется как боевой код (тот же
детектор/сегментация), этот скрипт добавляет ДИАГНОСТИКУ поверх выхода `segment_boxes()`
(gate=False, чтобы получить геометрию независимо от прохождения гейта v1) и тестирует
v2 = v1 AND новый_сигнал — новый сигнал может только СУЖАТЬ множество срабатываний v1,
никогда не расширять (v1 уже режет 100→~несколько кандидатов, задача v2 — дорезать их до 0).

Кандидаты сигналов (тимлид, 22.09 — все считаются на уже выбранном v1 ряду/колонках,
чистая геометрия, без новой модели):
  A. central_width_frac = ширина центральной колонки (кропа) / ширина кадра. Гипотеза:
     одиночная бутылка, ошибочно разрезанная на 2 "колонки" внутри своей этикетки, отдаёт
     БОЛЬШУЮ долю (весь кадр — одна бутылка, контракт `image-scan.md` держит её в
     0.15-0.85 ширины => уже ~70% занимает бутылка, половина от неё после ложного разреза
     — всё ещё много); настоящая полка (3+ колонки) делит кадр на много узких — узкую.
  B. n_cols / n_comparable_cols(frac) = число колонок, ЧЬЯ ВЫСОТА ОХВАТА БОКСОВ (верх-низ
     всех боксов, отнесённых к колонке) >= frac * высоты ряда — то есть колонка тянется
     почти на всю высоту ряда, как настоящая бутылка, а не обрывок текста сбоку. Порог >= 3
     колонок (сырых или "сопоставимых").
  C. structure_cv = коэффициент вариации (std/mean) МЕДИАННОЙ высоты бокса по колонкам —
     повторяемость структуры: одинаковые бутылки на полке дают близкие по высоте текстовые
     блоки между колонками; этикетка ОДНОЙ бутылки, ложно разрезанная на 2 "колонки", даёт
     РАЗНЫЕ (кусок сверху этикетки vs кусок снизу) высоты — высокий CV.
  D. text_aspect = ширина охвата ВСЕХ боксов ряда (max(x1)-min(x0), безотносительно того, на
     сколько колонок их потом разбили) / высота ряда. Настоящий ряд полки — широкий (много
     бутылок в ширину), одна бутылка — этикетка почти квадратная или выше, чем шире.

Данные (все офлайн, кэши/локальные фото — без живого API):
  100 фото организаторов — case-data/real-photos/*.webp (ВСЕ, включая 38 NONE, не только
    62 из каталога — задание тимлида явно про ВСЕ 100).
  32 полевых фото — Field/*.jpeg (ВСЕ).
  25 "настоящих полок" — 32 полевых МИНУС 6 unsure (F12/F15/F22/F24/F26/F31 — qa-manual сама
    не уверена в разметке/пригодности кадра, reports/qa-manual-field-photos.md) МИНУС 1
    побайтный дубль (F17=F16 по md5) = 23 sure + 3 likely - 1 дубль = 25. Совпадает с числом
    тимлида — see real_shelves_25() ниже, критерий воспроизводим из part3-field.csv.

Режимы:
  --diagnose [--limit N]   детекция+геометрия на 100+32 фото (~0.3-0.6с/фото), кэш ->
                           qa/scan-eval-runs/shelf-gate-v2/{organizer,field}_diag.json
  --sweep                  читает кэш, печатает таблицу сигнал/порог -> срабатываний
                           (100 организаторов / 32 полевых / 25 настоящих полок)
  --show PHOTO             печатает полную диагностику одного фото (по имени файла)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

CV_PKG = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv")
sys.path.insert(0, str(CV_PKG))
from cv.shelf_crop import (  # noqa: E402 — боевой код, читаем как есть, не правим
    DEFAULT_MIN_BOXES,
    ShelfDetector,
    intervals_xy,
    segment_boxes,
)

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
ORG_SRC = BASE / "real-photos"
FIELD_SRC = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/Field")
OUT = Path(__file__).resolve().parent / "scan-eval-runs" / "shelf-gate-v2"
OUT.mkdir(parents=True, exist_ok=True)

_DET = ShelfDetector()  # тот же боевой детектор (RapidOCR без распознавания, ленивый движок)


def load_rgb(path: Path) -> np.ndarray:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(im)


def real_shelves_25() -> list[str]:
    """32 полевых -> 25 "настоящих полок": confidence sure/likely (без 6 unsure, где
    qa-manual сама не уверена в разметке/пригодности кадра) минус побайтный дубль (md5) —
    qa-manual, reports/qa-manual-field-photos.md (23 sure + 3 likely + 6 unsure = 32)."""
    rows = list(csv.DictReader((LAB / "part3-field.csv").open(encoding="utf-8")))
    keep = [r["photo"] for r in rows if r["confidence"] in ("sure", "likely")]
    seen_md5: dict[str, str] = {}
    out: list[str] = []
    for name in keep:
        h = hashlib.md5((FIELD_SRC / name).read_bytes()).hexdigest()
        if h in seen_md5:
            continue
        seen_md5[h] = name
        out.append(name)
    return out


def diagnose(path: Path) -> dict:
    """Один проход: детекция + v1-геометрия (боевой `segment_boxes`, gate=False, чтобы
    получить крпы независимо от того, проходит ли гейт v1) + доп. диагностика для
    сигналов-кандидатов v2 (высоты по колонкам, ширина текстового охвата ряда) — считается
    ПОВЕРХ выхода боевой функции, саму функцию не переопределяет."""
    arr = load_rgb(path)
    h, w = arr.shape[:2]
    boxes = _DET.detect(arr)
    if len(boxes) == 0:
        return {"photo": path.name, "w": w, "h": h, "n_boxes": 0, "n_row_boxes": 0,
                "is_shelf_v1": False, "col_method": "none", "n_cols": 0,
                "central_width_frac": 1.0, "row_height": 0.0, "text_aspect": 0.0,
                "col_heights_frac": [], "col_median_box_h": []}
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    seg = segment_boxes(boxes, w, h, gray=gray, gate=False, min_boxes=DEFAULT_MIN_BOXES)

    x0, y0, x1, y1 = intervals_xy(boxes)
    ry0, ry1 = seg.crops[0][1], seg.crops[0][3]  # общий для всех колонок (см. shelf_crop.py)
    row_mask = (y1 > ry0) & (y0 < ry1)
    rx0, ry0b, rx1, ry1b = x0[row_mask], y0[row_mask], x1[row_mask], y1[row_mask]
    row_height = float(ry1 - ry0)
    row_text_width = float(rx1.max() - rx0.min()) if len(rx0) else 0.0

    col_heights_frac: list[float] = []
    col_median_box_h: list[float] = []
    centers_x = (rx0 + rx1) / 2 if len(rx0) else rx0
    for (cx0, _, cx1, _) in seg.crops:
        cmask = (centers_x >= float(cx0)) & (centers_x < float(cx1))
        if cmask.sum() == 0:
            col_heights_frac.append(0.0)
            col_median_box_h.append(0.0)
            continue
        col_span = float(ry1b[cmask].max() - ry0b[cmask].min())
        col_heights_frac.append(col_span / row_height if row_height > 0 else 0.0)
        col_median_box_h.append(float(np.median(ry1b[cmask] - ry0b[cmask])))

    central = seg.crops[seg.center_index]
    return {
        "photo": path.name, "w": w, "h": h, "n_boxes": int(len(boxes)),
        "n_row_boxes": seg.n_row_boxes, "is_shelf_v1": seg.is_shelf,
        "col_method": seg.col_method, "n_cols": len(seg.crops),
        "central_width_frac": (central[2] - central[0]) / w,
        "row_height": row_height,
        "text_aspect": (row_text_width / row_height) if row_height > 0 else 0.0,
        "col_heights_frac": col_heights_frac, "col_median_box_h": col_median_box_h,
    }


def cmd_diagnose(limit: int | None) -> None:
    org_photos = sorted(ORG_SRC.glob("*.webp"))
    field_photos = sorted(FIELD_SRC.glob("*.jpeg"))
    if limit:
        org_photos, field_photos = org_photos[:limit], field_photos[:limit]
    print(f"организаторы: {len(org_photos)} фото; полевые: {len(field_photos)} фото")

    for tag, photos in (("organizer", org_photos), ("field", field_photos)):
        out: dict[str, dict] = {}
        t0 = time.time()
        for i, p in enumerate(photos, 1):
            try:
                out[p.name] = diagnose(p)
            except Exception as exc:  # noqa: BLE001 — офлайн-прогон, не роняем весь батч
                print(f"  ! {p.name}: {exc!r}")
                out[p.name] = {"photo": p.name, "error": repr(exc)}
            if i % 20 == 0:
                print(f"  {tag} {i}/{len(photos)} ({time.time() - t0:.0f}s)", flush=True)
        (OUT / f"{tag}_diag.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
        n_shelf_v1 = sum(1 for d in out.values() if d.get("is_shelf_v1"))
        print(f"{tag}: гейт v1 сработал {n_shelf_v1}/{len(out)} ({time.time() - t0:.0f}s)")


def n_comparable(d: dict, frac: float) -> int:
    return sum(1 for chf in d["col_heights_frac"] if chf >= frac)


def structure_cv(d: dict) -> float | None:
    vals = [v for v in d["col_median_box_h"] if v > 0]
    if len(vals) < 2:
        return None
    m = statistics.mean(vals)
    if m <= 0:
        return None
    return statistics.pstdev(vals) / m


SIGNALS = {
    # (label, порог, predicate(d) -> bool "сигнал ЗА полку", условие в скобках — что значит порог)
    **{f"A central_width_frac < {t}": (t, lambda d, t=t: d["central_width_frac"] < t)
       for t in (0.30, 0.35, 0.40, 0.45, 0.50)},
    **{f"B n_cols >= 3 (raw)": (None, lambda d: d["n_cols"] >= 3)},
    **{f"B n_comparable_cols(>= {f} row_h) >= 3": (f, lambda d, f=f: n_comparable(d, f) >= 3)
       for f in (0.4, 0.5, 0.6)},
    **{f"C structure_cv <= {t}": (t, lambda d, t=t: (structure_cv(d) is not None and structure_cv(d) <= t))
       for t in (0.25, 0.35, 0.50, 0.70)},
    **{f"D text_aspect >= {t}": (t, lambda d, t=t: d["text_aspect"] >= t)
       for t in (1.10, 1.12, 1.15, 1.20, 1.30, 1.50, 2.0, 2.5, 3.0)},
}


def cmd_target8() -> None:
    """8 целевых полевых (part3-field.csv, sure/likely, true_slug != NONE) — те же 8, что
    reports/ml-lead-shelf-crop.md/reports/ml-eng-ml2.md; печатает, проходят ли v1 и
    text_aspect>=1.2 (рекомендуемый порог), чтобы решение было видно на именных кадрах, а
    не только в агрегате."""
    rows = list(csv.DictReader((LAB / "part3-field.csv").open(encoding="utf-8")))
    target = [r for r in rows if r["confidence"] in ("sure", "likely") and r["true_slug"] != "NONE"]
    field = json.loads((OUT / "field_diag.json").read_text())
    print(f"{'n':4s} {'photo':38s} {'v1':6s} {'D>=1.2':7s} text_aspect central_width_frac")
    for r in target:
        d = field[r["photo"]]
        v2 = d["text_aspect"] >= 1.2
        print(f"{r['n']:4s} {r['photo'][:38]:38s} {str(d['is_shelf_v1']):6s} {str(v2):7s} "
              f"{d['text_aspect']:.2f}        {d['central_width_frac']:.3f}")


def cmd_sweep() -> None:
    org = json.loads((OUT / "organizer_diag.json").read_text())
    field = json.loads((OUT / "field_diag.json").read_text())
    real25 = real_shelves_25()
    missing = [n for n in real25 if n not in field]
    if missing:
        print(f"! {len(missing)} из 25 не в кэше field_diag.json (перезапусти --diagnose): {missing}")
    real25_d = [field[n] for n in real25 if n in field]

    def v1(d):
        return bool(d.get("is_shelf_v1"))

    org_v1 = [d for d in org.values() if v1(d)]
    field_v1 = [d for d in field.values() if v1(d)]
    real25_v1 = [d for d in real25_d if v1(d)]
    print(f"v1 БЕЗ доп. сигнала: 100 организаторов {len(org_v1)}/100, "
          f"32 полевых {len(field_v1)}/32, 25 настоящих полок {len(real25_v1)}/25")
    if org_v1:
        print("  организаторы, где сработал v1 (кандидаты на устранение v2):")
        for d in org_v1:
            print(f"    {d['photo']:45s} n_cols={d['n_cols']} n_row_boxes={d['n_row_boxes']} "
                  f"col_method={d['col_method']} central_width_frac={d['central_width_frac']:.3f} "
                  f"text_aspect={d['text_aspect']:.2f} structure_cv={structure_cv(d)}")
    print()
    header = f"{'сигнал/порог':42s} {'100 орг(=0?)':13s} {'32 полевых':11s} {'25 полок':9s}"
    print(header)
    print("-" * len(header))
    print(f"{'(без v2 — только v1)':42s} {len(org_v1):<13d} {len(field_v1):<11d} {len(real25_v1):<9d}")
    for label, (_thr, pred) in SIGNALS.items():
        o = sum(1 for d in org_v1 if pred(d))
        f = sum(1 for d in field_v1 if pred(d))
        r = sum(1 for d in real25_v1 if pred(d))
        flag = "  <-- 0!" if o == 0 else ""
        print(f"{label:42s} {o:<13d} {f:<11d} {r:<9d}{flag}")


def cmd_show(name: str) -> None:
    for tag in ("organizer_diag.json", "field_diag.json"):
        data = json.loads((OUT / tag).read_text())
        if name in data:
            d = data[name]
            print(json.dumps(d, ensure_ascii=False, indent=1))
            print("structure_cv =", structure_cv(d))
            return
    print(f"{name} не найден в кэшах — запусти --diagnose")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--show", type=str, default=None)
    ap.add_argument("--target8", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    if a.diagnose:
        cmd_diagnose(a.limit)
    if a.sweep:
        cmd_sweep()
    if a.show:
        cmd_show(a.show)
    if a.target8:
        cmd_target8()
    print(f"[{time.time() - t0:.1f}s]", file=sys.stderr)
