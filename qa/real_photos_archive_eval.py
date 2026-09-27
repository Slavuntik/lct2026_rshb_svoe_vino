"""Вторая выборка: архив реальных сканов со стенда (`case-data/scan-archive/scans`),
разобранный глазами qa-manual (`reports/qa-manual-scan-archive.md`) — 119 уникальных
фото с человеческим вердиктом по каждому.

Два режима.

1. `--replay` (по умолчанию) — БЕЗ пересчёта: гейт переигрывается по сайдкарам
   (`top1_score` = CV-скор top-1, `gap` = family-gap по final, `ocr_verified`), ровно той
   же формулой, что `app/cv/service.py::_run_photo_scan_fusion`. Проверено: при
   floor=0.80 / gap=0.03 реплей воспроизводит корзину архива 253/253 записей.
   Годится для развёртки ПОРОГА (порог не меняет ранжирование — только гейт).

2. `--recompute` — полный офлайн-прогон боевого пути на кадрах архива: SigLIP2
   base-384 (norm+raw) по своей копии индекса `features/index_vectors_base384_d1.npy`
   + RapidOCR 640/960 + кроп 1280 + `cv.text_fusion.fuse()`. Нужен, когда меняем саму
   формулу (напр. штраф цвета), а не порог. Результат кэшируется в `--cache` (JSONL,
   вне git), повторный запуск читает кэш.

Вердикты qa-manual парсятся прямо из `reports/qa-manual-scan-archive.md` (таблицы
C01…/U01…/F01…), поэтому таблица истины не дублируется в коде.

Классы истины (по вердикту человека):
  in_ok  — вино ЕСТЬ в каталоге и top-1 системы верен  (верно / ПРОПУСК с верным top-1)
  in_bad — вино есть, но top-1 неверен                 (2 фото: U08, F11)
  none   — правильный ответ «нет в каталоге»           (верный отказ / ОШИБКА / близнец / не вино)
  skip   — «не определить» (U07, микро-рендер 102x500)

Запуск (из packages/cv):
  .venv/bin/python ../../qa/real_photos_archive_eval.py --replay
  .venv/bin/python ../../qa/real_photos_archive_eval.py --recompute --cache /tmp/arc.jsonl
  .venv/bin/python ../../qa/real_photos_archive_eval.py --recompute --cache /tmp/arc.jsonl \
      --color-penalty 0.05 0.10 0.15
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
ARCHIVE = BASE / "scan-archive" / "scans"
REPORT = Path(__file__).resolve().parents[1] / "reports" / "qa-manual-scan-archive.md"

GAP_FLOOR = 0.03
FLOORS = (0.76, 0.77, 0.78, 0.79, 0.80, 0.82)
# ПРОПУСК, где правильный слаг НЕ был top-1 (reports/qa-manual-scan-archive.md,
# «Правильный слаг был не top-1») — понижение порога делает их уверенной ОШИБКОЙ.
TOP1_WRONG = {"20260923T145618_f56661", "20260922T120422_045363"}
CLS = {
    "верно": "in_ok", "ПРОПУСК": "in_ok",
    "ОШИБКА": "none", "близнец": "none", "верный отказ": "none", "не вино": "none",
    "не определить": "skip",
}


def load_verdicts() -> list[dict]:
    rows = []
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([CUF]\d\d)\s*\|\s*`([^`]+)\.jpg`\s*\|(.*)$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(3).split("|")]
        rid = m.group(2)
        cls = CLS[cells[2]]
        rows.append({"qid": m.group(1), "id": rid, "label": cells[0], "served": cells[1],
                     "verdict": cells[2], "cls": "in_bad" if (cls == "in_ok" and rid in TOP1_WRONG) else cls})
    assert len(rows) == 119, f"ожидали 119 строк вердиктов, получили {len(rows)}"
    return rows


def load_sidecars() -> dict[str, dict]:
    out = {}
    for line in (ARCHIVE / "index.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["id"]] = json.loads((ARCHIVE / (r["path"] + ".json")).read_text(encoding="utf-8"))
            out[r["id"]]["_path"] = r["path"]
    return out


def confident(cv_score, gap, floor, ocr_verified=False) -> bool:
    return (cv_score is not None and cv_score >= floor
            and (ocr_verified or gap is None or gap >= GAP_FLOOR))


def sanity_replay(side: dict) -> None:
    bad = sum(1 for s in side.values()
              if confident(s["top1_score"], s.get("gap"), 0.80, s.get("ocr_verified")) != (s["bucket"] == "confident"))
    print(f"сверка реплея гейта на floor=0.80: расхождений {bad}/{len(side)}")
    assert bad == 0, "реплей не воспроизводит корзины архива — формула гейта разошлась с боевой"


def sweep(rows: list[dict], score_of, gap_of, ocrv_of, title: str) -> dict:
    n_in = sum(r["cls"] in ("in_ok", "in_bad") for r in rows)
    n_none = sum(r["cls"] == "none" for r in rows)
    print(f"\n=== {title} === (вино в каталоге: {n_in}, правильный ответ «нет»: {n_none}, "
          f"не определить: {sum(r['cls'] == 'skip' for r in rows)})")
    print(f"{'floor':>5} | {'верных карточек':>15} | {'честных «нет»':>13} | {'уверенных ошибок':>16} | "
          f"{'пропусков (вино есть, отказ)':>28}")
    out = {}
    for f in FLOORS:
        ok = hon = err = miss = 0
        for r in rows:
            if r["cls"] == "skip":
                continue
            c = confident(score_of(r), gap_of(r), f, ocrv_of(r))
            if r["cls"] == "in_ok":
                ok += c
                miss += not c
            elif r["cls"] == "in_bad":
                err += c
                miss += not c
            else:
                err += c
                hon += not c
        out[f] = (ok, hon, err, miss)
        print(f"{f:5.2f} | {ok:15d} | {hon:13d} | {err:16d} | {miss:28d}")
    return out


def names(rows, score_of, gap_of, ocrv_of, floor: float) -> None:
    plus, minus = [], []
    for r in rows:
        if r["cls"] == "skip":
            continue
        c, c80 = (confident(score_of(r), gap_of(r), f, ocrv_of(r)) for f in (floor, 0.80))
        if c and not c80:
            (plus if r["cls"] == "in_ok" else minus).append(r)
    print(f"  floor {floor}: +{len(plus)} верных карточек, +{len(minus)} уверенных ошибок")
    for r in sorted(plus, key=lambda x: -score_of(x)):
        print(f"    + {r['qid']} {score_of(r):.4f}  {r['label'][:58]} -> {r['served'][:48]}")
    for r in sorted(minus, key=lambda x: -score_of(x)):
        print(f"    - {r['qid']} {score_of(r):.4f}  [{r['verdict']}] {r['label'][:52]} -> {r['served'][:44]}")


# ---------------------------------------------------------------------------
# --recompute: боевой путь на кадрах архива
# ---------------------------------------------------------------------------

def recompute(rows: list[dict], side: dict, cache: Path, color_penalties: list[float]) -> dict:
    import os

    import numpy as np

    os.environ.setdefault("CV_OCR_ENGINE", "rapid")
    os.environ.setdefault("CV_OCR_RAPID_SIZES", "640,960")
    os.environ.setdefault("CV_OCR_LABEL_SIZE", "1280")
    os.environ.setdefault("CV_MODEL", "google/siglip2-base-patch16-384")

    from cv import families as cv_families
    from cv import imageio, text_fusion
    from cv.encoder import SiglipEncoder
    from cv.normalize import normalize_query
    from cv.verify import LabelVerifier

    from real_photos_choose_rule import ANN_TOP_K, CSV_PATH, FEAT, TEXT_TOP_N, W

    meta = json.loads((FEAT / "index_meta_base384_d1.json").read_text())
    vs = meta["slugs"]
    slugs = sorted(set(vs))
    pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in vs])
    V = np.load(FEAT / "index_vectors_base384_d1.npy").astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)

    text_index = text_fusion.load_catalog_index(str(CSV_PATH))
    winery_index = text_fusion.load_winery_index(str(CSV_PATH), aliases_json=str(BASE / "winery_aliases.json"))
    colors = text_fusion.color_by_slug(text_index)
    fam = cv_families.load_family_by_slug(BASE / "families.json")

    done = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done[d["id"]] = d
    todo = [r for r in rows if r["id"] not in done]
    if todo:
        enc, ver = SiglipEncoder(), LabelVerifier(engine="rapid", rapid_sizes=(640, 960))

        def emb(x):
            v = np.asarray(enc.encode(np.ascontiguousarray(x)), dtype=np.float32)
            return v / np.linalg.norm(v)

        with cache.open("a", encoding="utf-8") as fh:
            for i, r in enumerate(todo, 1):
                raw = (ARCHIVE / (side[r["id"]]["_path"] + ".jpg")).read_bytes()
                arr = imageio.decode_image(raw)
                q = np.stack([emb(arr), emb(normalize_query(arr, enabled=True))])
                row = np.full(len(slugs), -1.0, dtype=np.float32)
                np.maximum.at(row, vsi, (q @ V.T).max(axis=0))
                text = ver.read_query_text(raw)
                # мимикрия ImageIndex.search_fusion(): ANN top-K ∪ ТОЧНЫЙ скор текстовых
                # extra_slugs — их и кладём в кэш, иначе fuse() подставит заглушку cv_pad.
                _, mass = text_index.scores(text)
                keep = {slugs[j] for j in np.argsort(-row)[:ANN_TOP_K]}
                keep |= {s for s in text_fusion.top_text_slugs(text_index, mass, TEXT_TOP_N) if s in pos}
                d = {"id": r["id"], "text": text,
                     "cv": {s: round(float(row[pos[s]]), 6) for s in keep}}
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                fh.flush()
                done[r["id"]] = d
                if i % 10 == 0:
                    print(f"  пересчёт {i}/{len(todo)}", flush=True)

    res = {}
    for pen in color_penalties:
        per = {}
        for r in rows:
            d = done[r["id"]]
            cv = dict(d["cv"])
            fr = text_fusion.fuse(cv, text_index, d["text"], family_by_slug=fam, w=W,
                                  winery_index=winery_index, unconfirmed_winery_w=0.5,
                                  colors=colors, color_penalty=pen)
            per[r["id"]] = (fr.ranked[0].slug, fr.ranked[0].cv_score, fr.gap) if fr.ranked else (None, None, None)
        res[pen] = per
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recompute", action="store_true")
    ap.add_argument("--cache", default="/tmp/archive_recompute.jsonl")
    ap.add_argument("--color-penalty", nargs="*", type=float, default=[0.05])
    ap.add_argument("--names", action="store_true")
    a = ap.parse_args()

    rows, side = load_verdicts(), load_sidecars()
    sanity_replay(side)
    md5 = collections.Counter(hashlib.md5((ARCHIVE / (s["_path"] + ".jpg")).read_bytes()).hexdigest()
                              for s in side.values())
    print(f"записей {len(side)}, уникальных кадров {len(md5)}, вердиктов {len(rows)}")

    sweep(rows, lambda r: side[r["id"]]["top1_score"], lambda r: side[r["id"]].get("gap"),
          lambda r: side[r["id"]].get("ocr_verified"), "реплей гейта по сайдкарам (боевой ответ стенда)")
    if a.names:
        for f in (0.79, 0.78, 0.77, 0.76):
            names(rows, lambda r: side[r["id"]]["top1_score"], lambda r: side[r["id"]].get("gap"),
                  lambda r: side[r["id"]].get("ocr_verified"), f)

    if a.recompute:
        res = recompute(rows, side, Path(a.cache), a.color_penalty)
        for pen, per in res.items():
            agree = sum(per[r["id"]][0] == side[r["id"]]["predicted_slug"] for r in rows)
            print(f"\nштраф цвета {pen}: top-1 совпал с боевым ответом стенда на {agree}/{len(rows)} кадрах")
            sweep(rows, lambda r, p=per: p[r["id"]][1], lambda r, p=per: p[r["id"]][2], lambda r: False,
                  f"пересчёт офлайн, color_penalty={pen}")


if __name__ == "__main__":
    main()
