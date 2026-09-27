"""Развёртка порога уверенности (CV_FUSION_CV_FLOOR, «пол» гейта) на 100 живых фото
организаторов — обе стороны сразу: 62 фото с вином из каталога (уверенный ВЕРНЫЙ top-1)
и 38 фото без вина в каталоге (честный отказ — ПРАВИЛЬНЫЙ ответ).

Формула слияния — боевая (`cv.text_fusion.fuse()`), данные — те же кэши features/,
что у `qa/real_photos_choose_rule.py` (оттуда же Fuser/загрузчики, не копия). Пол —
единственное, что двигаем: `confident = ranked[0].cv_score >= floor И (gap is None или
gap >= CV_FUSION_GAP_FLOOR)`, ровно как `app/cv/service.py::_run_photo_scan_fusion`
(ветка `ocr_verified` выключена: CV_FUSION_VERIFY=0 и на стенде, и в fuse()).

ВАЖНО (проверено по коду 27.09): порог НЕ влияет на метрику приватной проверки.
`/v1/eval/predict` и `?flat=1` отдают `best_guess_slug`, который ставится ДО гейта
(`service.py:890`) и гейтом не обнуляется (единственное исключение — вето «не бутылка»).
Гейт меняет только rich-ответ (карточка против экрана кандидатов) и корзину архива.
Поэтому колонка «верных top-1 из 62» ниже — это «уверенно показанная верная карточка»,
а не то, что считает скрипт кейсодержателя; для него — строка `flat (без гейта)`.

`--field` — ТРЕТЬЯ, действительно независимая выборка: 32 полевых фото полок Вячеслава
(`~/ClaudeWorkspace/vines/Field`, разметка `real-photos-labels/part3-field.csv`, F16=F17 —
байт-в-байт дубль, считаем 31 кадр). Боевой путь целиком (SigLIP2 base-384 norm+raw по своей
копии индекса + RapidOCR 640/960 + кроп 1280 + fuse); CV_SHELF_CROP=0, как в бою.

Запуск (из packages/cv):
  .venv/bin/python ../../qa/real_photos_floor_sweep.py
  .venv/bin/python ../../qa/real_photos_floor_sweep.py --sources live_verifier vlm --names
  .venv/bin/python ../../qa/real_photos_floor_sweep.py --field --cache /tmp/field.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from real_photos_archive_eval import GAP_FLOOR, confident  # noqa: E402  — один гейт на обе выборки
from real_photos_choose_rule import (  # noqa: E402
    TEXT_TOP_N,
    W,
    Fuser,
    build_ocr_text,
    load_cv,
    load_jsonl_text,
    load_labels,
)

# Источники текста этикетки: боевой OCR-путь + три «читателя» для проверки, что правило
# не подогнано под один источник (тимлид: OCR / 27B / 4B / человек).
SOURCES = {
    "live_verifier": "ocr",   # боевой RapidOCR 640+960 + кроп 1280 (CPU-путь стенда)
    "vlm": "model",           # Qwen3-VL-27B через шлюз
    "q4b_640": "model",       # Qwen3-VL-4B локально
    "human": "model",         # человек-читатель (потолок текста)
}
FLOORS = (0.76, 0.77, 0.78, 0.79, 0.80, 0.82)
# GAP_FLOOR / confident() — из qa/real_photos_archive_eval.py: гейт обязан быть ОДНИМ
# и тем же на обеих выборках (100 фото организаторов и архив сканов стенда).


def run_source(fs: Fuser, idx_of, photos, tag: str, color_penalty: float) -> dict:
    """{photo: (top1_slug, cv_score, gap)} для одного источника текста."""
    import cv.text_fusion as tf  # штраф цвета Fuser берёт из модуля qa/real_photos_choose_rule

    text = build_ocr_text(photos) if tag == "live_verifier" else load_jsonl_text(tag)
    w = 0.5 if SOURCES[tag] == "ocr" else 1.0
    out = {}
    for p in photos:
        _, mass = fs.text_index.scores(text.get(p, ""))
        ttop = tf.top_text_slugs(fs.text_index, mass, TEXT_TOP_N)
        r = tf.fuse(fs._cv_scores(idx_of[p], ttop), fs.text_index, text.get(p, ""), family_by_slug=fs.fam,
                    w=W, winery_index=fs.winery_index, unconfirmed_winery_w=w,
                    colors=fs.colors, color_penalty=color_penalty)
        out[p] = (r.ranked[0].slug, r.ranked[0].cv_score, r.gap) if r.ranked else (None, None, None)
    return out


# ---------------------------------------------------------------------------
# --field: 32 полевых фото полок (независимая выборка Вячеслава)
# ---------------------------------------------------------------------------

FIELD_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/Field")
FIELD_CSV = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/real-photos-labels/part3-field.csv")


def run_field(cache: Path, color_penalties: list[float]) -> None:
    import csv
    import hashlib
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

    from real_photos_choose_rule import ANN_TOP_K, BASE, CSV_PATH, FEAT

    rows = list(csv.DictReader(FIELD_CSV.open(newline="", encoding="utf-8")))
    seen: dict[str, str] = {}
    uniq = []
    for r in rows:  # F16=F17 — байт-в-байт дубль кадра, считаем один раз
        h = hashlib.md5((FIELD_DIR / r["photo"]).read_bytes()).hexdigest()
        if h in seen:
            print(f"  дубль кадра: {r['n']} = {seen[h]}, пропускаю")
            continue
        seen[h] = r["n"]
        uniq.append(r)
    print(f"полевых фото: {len(rows)} файлов, {len(uniq)} уникальных кадров; "
          f"с вином в каталоге {sum(r['true_slug'] not in ('', 'NONE') for r in uniq)}")

    meta = json.loads((FEAT / "index_meta_base384_d1.json").read_text())
    slugs = sorted(set(meta["slugs"]))
    pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in meta["slugs"]])
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
                done[d["n"]] = d
    todo = [r for r in uniq if r["n"] not in done]
    if todo:
        enc, ver = SiglipEncoder(), LabelVerifier(engine="rapid", rapid_sizes=(640, 960))

        def emb(x):
            v = np.asarray(enc.encode(np.ascontiguousarray(x)), dtype=np.float32)
            return v / np.linalg.norm(v)

        with cache.open("a", encoding="utf-8") as fh:
            for i, r in enumerate(todo, 1):
                raw = (FIELD_DIR / r["photo"]).read_bytes()
                arr = imageio.decode_image(raw)
                q = np.stack([emb(arr), emb(normalize_query(arr, enabled=True))])
                row = np.full(len(slugs), -1.0, dtype=np.float32)
                np.maximum.at(row, vsi, (q @ V.T).max(axis=0))
                text = ver.read_query_text(raw)
                _, mass = text_index.scores(text)
                keep = {slugs[j] for j in np.argsort(-row)[:ANN_TOP_K]}
                keep |= {s for s in text_fusion.top_text_slugs(text_index, mass, TEXT_TOP_N) if s in pos}
                d = {"n": r["n"], "text": text, "cv": {s: round(float(row[pos[s]]), 6) for s in keep}}
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
                fh.flush()
                done[r["n"]] = d
                print(f"  поле {i}/{len(todo)} ({r['n']})", flush=True)

    for pen in color_penalties:
        res = {}
        for r in uniq:
            d = done[r["n"]]
            fr = text_fusion.fuse(dict(d["cv"]), text_index, d["text"], family_by_slug=fam, w=W,
                                  winery_index=winery_index, unconfirmed_winery_w=0.5,
                                  colors=colors, color_penalty=pen)
            res[r["n"]] = (fr.ranked[0].slug, fr.ranked[0].cv_score, fr.gap) if fr.ranked else (None, None, None)
        inc = [r for r in uniq if r["true_slug"] not in ("", "NONE")]
        non = [r for r in uniq if r["true_slug"] in ("", "NONE")]
        flat = sum(res[r["n"]][0] == r["true_slug"] for r in inc)
        print(f"\n=== полевые полки, штраф цвета {pen} === (в каталоге {len(inc)}, честных NONE {len(non)})")
        print(f"flat (без гейта): {flat}/{len(inc)}")
        print(f"{'floor':>5} | {'верных карточек':>15} | {'честных «нет»':>13} | {'уверенных ошибок':>16}")
        for f in FLOORS:
            ok = sum(confident(*res[r["n"]][1:], f) and res[r["n"]][0] == r["true_slug"] for r in inc)
            hon = sum(not confident(*res[r["n"]][1:], f) for r in non)
            err = sum(confident(*res[r["n"]][1:], f) and res[r["n"]][0] != r["true_slug"] for r in uniq)
            print(f"{f:5.2f} | {ok:15d} | {hon:13d} | {err:16d}")
        print("  кадры в полосе 0.74–0.84 (только они и могут переключиться):")
        for r in sorted(uniq, key=lambda x: -(res[x["n"]][1] or 0)):
            s, cv, g = res[r["n"]]
            if cv is not None and 0.74 <= cv <= 0.84:
                print(f"    {r['n']} cv={cv:.4f} gap={'None' if g is None else f'{g:.4f}'} "
                      f"истина={r['true_slug'][:26]:26s} top1={s[:40]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="*", default=list(SOURCES))
    ap.add_argument("--names", action="store_true", help="поимённо: что выигрывается и что теряется против 0.80")
    ap.add_argument("--color-penalty", nargs="*", type=float, default=[0.05],
                    help="CV_FUSION_COLOR_PENALTY: боевое 0.05; несколько значений — A/B")
    ap.add_argument("--field", action="store_true", help="независимая выборка: 32 полевых фото полок")
    ap.add_argument("--cache", default="/tmp/field_sweep.jsonl", help="кэш пересчёта для --field (вне git)")
    ap.add_argument("--dump-json", default="")
    a = ap.parse_args()

    if a.field:
        run_field(Path(a.cache), a.color_penalty)
        return

    photos, idx_of, labels, in_cat, none_photos = load_labels()
    CV, slugs, pos = load_cv(len(photos))
    fs = Fuser(CV, slugs, pos)
    print(f"фото: {len(photos)} (в каталоге {len(in_cat)}, честных NONE {len(none_photos)}); "
          f"gap_floor={GAP_FLOOR}, формула cv+0.3*rel")

    dump = {}
    for tag in a.sources:
      for pen in a.color_penalty:
        res = run_source(fs, idx_of, photos, tag, pen)
        flat = sum(res[p][0] == labels[p] for p in in_cat)
        print(f"\n=== источник текста: {tag} ({SOURCES[tag]}), штраф цвета {pen} ===")
        print(f"flat (без гейта, метрика приватной проверки): {flat}/{len(in_cat)} = {flat / len(in_cat):.1%}")
        print(f"{'floor':>5} | {'верных карточек /62':>19} | {'честных «нет» /38':>17} | "
              f"{'уверенных ошибок':>16} | {'пропусков (вино есть, отказ)':>28}")
        rows = {}
        for f in FLOORS:
            ok = sum(confident(res[p][1], res[p][2], f) and res[p][0] == labels[p] for p in in_cat)
            miss = len(in_cat) - sum(confident(res[p][1], res[p][2], f) for p in in_cat)
            wrong_conf = sum(confident(res[p][1], res[p][2], f) and res[p][0] != labels[p] for p in in_cat)
            honest = sum(not confident(res[p][1], res[p][2], f) for p in none_photos)
            wrong_conf += len(none_photos) - honest
            rows[f] = (ok, honest, wrong_conf, miss)
            print(f"{f:5.2f} | {ok:19d} | {honest:17d} | {wrong_conf:16d} | {miss:28d}")
        dump[f"{tag}@{pen}"] = {"flat": flat, "rows": {str(k): v for k, v in rows.items()},
                                "per_photo": {p: [res[p][0], res[p][1], res[p][2]] for p in photos}}

        if a.names:
            for f in FLOORS:
                if f >= 0.80:
                    continue
                gained_ok, gained_bad = [], []
                for p in photos:
                    sl, cv, g = res[p]
                    if confident(cv, g, f) and not confident(cv, g, 0.80):
                        (gained_ok if (p in in_cat and sl == labels[p]) else gained_bad).append((p, cv, sl))
                if not (gained_ok or gained_bad):
                    continue
                print(f"  floor {f}: +{len(gained_ok)} верных карточек, +{len(gained_bad)} уверенных ошибок")
                for p, cv, sl in sorted(gained_ok, key=lambda x: -x[1]):
                    print(f"    + {p}  cv={cv:.4f}  {sl}")
                for p, cv, sl in sorted(gained_bad, key=lambda x: -x[1]):
                    print(f"    - {p}  cv={cv:.4f}  истина={labels[p]}  выдано={sl}")

    if a.dump_json:
        Path(a.dump_json).write_text(json.dumps(dump, ensure_ascii=False))
        print(f"\nдамп: {a.dump_json}")


if __name__ == "__main__":
    main()
