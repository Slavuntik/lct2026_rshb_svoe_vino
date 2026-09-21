"""Офлайн-оценка схем CV + текст на размеченных реальных фото кейса.

Вход (вне git): case-data/real-photos-labels/part*.csv (разметка L1/L2) и
features/ (qa/real_photos_features.py). Метрика — как в приватной проверке: raw top-1
по точному слагу (плюс top-5 и «с точностью до семьи» — справочно).

Запуск из packages/cv:  .venv/bin/python ../../qa/real_photos_eval.py [--conf sure,likely]
"""
import argparse, csv, json
from pathlib import Path

import numpy as np

from cv import families, text_rerank as tr

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from text_v2 import TextIndexV2, sugar_of  # noqa: E402

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = BASE / "real-photos-labels"
FEAT = LAB / "features"


def load_labels(confs):
    rows = {}
    for p in sorted(LAB.glob("part*.csv")):
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows[r["photo"]] = r
    return {k: v for k, v in rows.items() if v["confidence"] in confs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", default="sure,likely")
    ap.add_argument("--ocr", default="crop320,crop640,full640")
    ap.add_argument("--dump", action="store_true", help="построчно: истина, CV top-1, лучшая схема")
    ap.add_argument("--served", nargs="*", default=[], help="JSONL qa/real_photos_serve.py — сравнить на той же разметке")
    a = ap.parse_args()

    labels = load_labels(set(a.conf.split(",")))
    photos = json.loads((FEAT / "photos.json").read_text())
    slugs = json.loads((FEAT / "slugs.json").read_text())
    sims = {f.stem[5:]: np.load(f) for f in FEAT.glob("sims_*.npy")}
    S = {"norm": sims["norm"], "raw": sims["raw"]}
    S["mean"] = (sims["norm"] + sims["raw"]) / 2
    S["max"] = np.maximum(sims["norm"], sims["raw"])
    crops = sorted({k.rsplit("_", 1)[0] for k in sims if "_" in k})
    for c in crops:
        S[f"max+{c}"] = np.max(np.stack([sims["norm"], sims["raw"], sims[f"{c}_norm"], sims[f"{c}_raw"]]), axis=0)
    if crops:
        allv = np.stack(list(sims.values()))
        S["maxall"] = allv.max(axis=0)
        S["meanall"] = allv.mean(axis=0)
        S["maxcrops"] = np.max(np.stack([sims[f"{c}_{m}"] for c in crops for m in ("norm", "raw")]), axis=0)

    catalog = tr.load_catalog_text(BASE / "strapi_output0709.csv")
    idf = tr.build_idf(catalog)
    gate = tr.distinctive_idf_threshold(idf)
    # глобальное пространство кандидатов: индексированные слаги + слаги каталога без эталона
    extra = sorted(set(catalog) - set(slugs))
    all_slugs = slugs + extra
    fam_by = families.load_family_by_slug(BASE / "families.json")

    # доп. поля кандидата для v2: категория и маркер сахара (слаг -> имя фото -> название)
    photo_names, category = {}, {}
    with (BASE / "strapi_output0709.csv").open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            s_ = (r.get("Slug") or "").strip()
            if s_:
                photo_names.setdefault(s_, []).append(r.get("Название фото") or "")
                category.setdefault(s_, r.get("Категория") or "")
    extra_f = {s_: {"category": category.get(s_, ""),
                    "sugar": sugar_of(s_, photo_names.get(s_, []), catalog[s_].name if s_ in catalog else "")}
               for s_ in all_slugs}
    FIELDSETS = {"nwg": ("name", "winery", "grape"), "nwgcs": ("name", "winery", "grape", "category", "sugar"),
                 "nws": ("name", "winery", "sugar")}
    tv2s = {k: TextIndexV2({s_: catalog[s_] for s_ in all_slugs if s_ in catalog}, fields=f, extra=extra_f)
            for k, f in FIELDSETS.items()}
    map_v2 = {k: np.array([{s_: i for i, s_ in enumerate(t.slugs)}.get(s_, -1) for s_ in all_slugs])
              for k, t in tv2s.items()}
    v2cache = {}

    def v2vec(v, p, mode, fs="nwg"):
        key = (v, p, fs)
        if key not in v2cache:
            rec, mass = tv2s[fs].scores(ocr[v].get(p, ""))
            rec = np.array(rec + [0.0], dtype=np.float32)[map_v2[fs]]
            mass = np.array(mass + [0.0], dtype=np.float32)[map_v2[fs]]
            v2cache[key] = (rec, mass)
        rec, mass = v2cache[key]
        rel = mass / mass.max() if mass.max() > 0 else mass
        return {"rec": rec, "rel": rel, "geo": np.sqrt(rec * rel)}[mode]

    ocr = {}
    for v in a.ocr.split(","):
        f = FEAT / f"ocr_{v}.jsonl"
        if f.exists():
            ocr[v] = {json.loads(l)["photo"]: json.loads(l)["text"] for l in f.read_text().splitlines() if l.strip()}

    idx_of = {p: i for i, p in enumerate(photos)}
    in_cat = [p for p, r in labels.items() if r["true_slug"] not in ("", "NONE") and p in idx_of]
    none_cnt = sum(1 for r in labels.values() if r["true_slug"] == "NONE")
    unknown_truth = [p for p in in_cat if labels[p]["true_slug"] not in catalog]
    print(f"размечено ({a.conf}): {len(labels)}; в каталоге: {len(in_cat)}; NONE: {none_cnt}; "
          f"истина без эталона в индексе: {sum(labels[p]['true_slug'] not in set(slugs) for p in in_cat)}; "
          f"слаг не найден в каталоге: {len(unknown_truth)}")
    # половины — чёт/нечет: файлы отсортированы по числу-префиксу (похоже на уверенность их
    # сканера), первые 50 заметно труднее вторых — делить подряд нечестно
    halves = {"A": [p for p in in_cat if idx_of[p] % 2 == 0], "B": [p for p in in_cat if idx_of[p] % 2 == 1]}

    # кэш текстовых скоров: (вариант, фото) -> вектор по all_slugs
    tcache = {}

    def tvec(v, p):
        key = (v, p)
        if key not in tcache:
            text = ocr[v].get(p, "")
            if not tr.has_distinctive_token(text, idf, gate):
                tcache[key] = np.zeros(len(all_slugs), dtype=np.float32)
            else:
                tcache[key] = np.array([tr.text_score(text, catalog.get(s), idf) for s in all_slugs], dtype=np.float32)
        return tcache[key]

    def cv_full(kind, p, pad_mode="median"):
        cvv = S[kind][idx_of[p]]
        top = float(cvv.max())
        val = {"median": float(np.median(cvv)), "top-0.03": top - 0.03, "top-0.06": top - 0.06,
               "p99": float(np.percentile(cvv, 99))}[pad_mode]
        return np.concatenate([cvv, np.full(len(extra), val, dtype=np.float32)])

    def evaluate(rank_fn, subset):
        t1 = t5 = f1 = 0
        for p in subset:
            order = rank_fn(p)
            truth = labels[p]["true_slug"]
            top = [all_slugs[i] for i in order[:5]]
            t1 += top[0] == truth
            t5 += truth in top
            f1 += top[0] == truth or (truth in fam_by and fam_by.get(top[0]) == fam_by[truth])
        n = max(len(subset), 1)
        return t1 / n, t5 / n, f1 / n

    results = []

    def run(name, rank_fn):
        full = evaluate(rank_fn, in_cat)
        ha = evaluate(rank_fn, halves["A"])[0]
        hb = evaluate(rank_fn, halves["B"])[0]
        results.append((name, *full, ha, hb))

    for kind in S:
        run(f"cv_{kind}", lambda p, k=kind: np.argsort(-cv_full(k, p)))
    fuse_kinds = [k for k in ("norm", "max", "maxall", "meanall", "maxcrops") if k in S]

    for v in ocr:
        for kind in ("norm", "mean"):
            for k in (5, 10, 20):
                for w in (0.01, 0.03, 0.1):
                    def f(p, v=v, kind=kind, k=k, w=w):
                        cvv = cv_full(kind, p)
                        order = np.argsort(-cvv)
                        head = order[:k]
                        sc = cvv[head] + w * tvec(v, p)[head]
                        return np.concatenate([head[np.argsort(-sc, kind="stable")], order[k:]])
                    run(f"rerank_{v}_{kind}_k{k}_w{w}", f)
            for w in (0.02, 0.05, 0.1, 0.2, 0.5):
                run(f"global_{v}_{kind}_w{w}",
                    lambda p, v=v, kind=kind, w=w: np.argsort(-(cv_full(kind, p) + w * tvec(v, p)), kind="stable"))

    for v in ocr:
        for kind in fuse_kinds:
            for mode in ("rec", "rel", "geo"):
                for w in (0.02, 0.05, 0.1, 0.2, 0.3):
                    run(f"v2_{v}_{kind}_{mode}_w{w}",
                        lambda p, v=v, kind=kind, mode=mode, w=w: np.argsort(-(cv_full(kind, p) + w * v2vec(v, p, mode)), kind="stable"))

    for v in ocr:
        for kind in [k for k in ("max", "meanall") if k in S]:
            for fs in ("nwgcs", "nws"):
                for mode in ("rel", "geo"):
                    for pad in ("median", "p99", "top-0.06", "top-0.03"):
                        for w in (0.1, 0.2, 0.3):
                            run(f"v3_{v}_{kind}_{fs}_{mode}_{pad}_w{w}",
                                lambda p, v=v, kind=kind, fs=fs, mode=mode, pad=pad, w=w:
                                np.argsort(-(cv_full(kind, p, pad) + w * v2vec(v, p, mode, fs)), kind="stable"))

    for sp in a.served:
        rows = {json.loads(l)["photo"]: json.loads(l) for l in Path(sp).read_text().splitlines() if l.strip()}

        def served_eval(subset):
            t1 = t5 = f1 = 0
            for p in subset:
                r, truth = rows.get(p, {}), labels[p]["true_slug"]
                top5 = [m["slug"] for m in (r.get("matches") or [])]
                t1 += r.get("flat_slug") == truth
                t5 += truth in top5 or r.get("flat_slug") == truth
                f1 += r.get("flat_slug") == truth or (truth in fam_by and fam_by.get(r.get("flat_slug")) == fam_by[truth])
            n = max(len(subset), 1)
            return t1 / n, t5 / n, f1 / n
        missing = [p for p in in_cat if p not in rows]
        full = served_eval(in_cat)
        results.append((f"SERVED:{Path(sp).stem}" + (f" (нет {len(missing)})" if missing else ""), *full,
                        served_eval(halves["A"])[0], served_eval(halves["B"])[0]))

    results.sort(key=lambda r: -r[1])
    print(f"{'схема':42s} {'top1':>6s} {'top5':>6s} {'сем1':>6s} {'A':>6s} {'B':>6s}")
    base = [r for r in results if r[0] == "cv_norm"][0]
    served_rows = [r for r in results if r[0].startswith("SERVED")]
    for r in [base] + served_rows + results[:25]:
        print(f"{r[0]:42s} {r[1]:6.1%} {r[2]:6.1%} {r[3]:6.1%} {r[4]:6.1%} {r[5]:6.1%}")
    (FEAT / "eval_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
