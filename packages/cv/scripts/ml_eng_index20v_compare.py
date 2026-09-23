#!/usr/bin/env python3
"""packages/cv/scripts/ml_eng_index20v_compare.py — ml-engineer, бриф тимлида 22.09
("index-20-views"): офлайн-сравнение боевого индекса D1 (`case-20260921-d1-b384`,
~7 ракурсов/позицию — 1 real + 6 synth) против экспериментального индекса с
21 ракурсом/позицию (1 real + 20 synth, DoD-порог "≥20"), собранного ТЕМ ЖЕ
сборщиком (`scripts/g6_build_index.py`) и ТОЙ ЖЕ моделью (SigLIP2 base-384) —
меняется ТОЛЬКО число ракурсов, эталоны (`case-data/slug_refs.json`, D1-очищенные)
те же (см. docs/architecture/models-and-algorithms.md §5, "D1-индекс расходится
с DoD по числу ракурсов").

ЦЕЛИКОМ офлайн, без GPU-шлюза: SigLIP2 гоняется ЛОКАЛЬНО (MPS/CPU этой машины,
НЕ удалённый VLM 27B через шлюз — это разные модели/пути, см. модели-и-алгоритмы
§1.1 vs §1.4). Слияние CV+текст — ЧЕРЕЗ боевую `cv.text_fusion.fuse()` (не
переизобретаем формулу), мимикрия `qa/real_photos_choose_rule.py` — константы
W/COLOR_PENALTY/*_UNCONFIRMED_W/ANN_TOP_K/TEXT_TOP_N идентичны боевым
(`infra/ams3/somelye.env.example`, сверено 22.09).

## Три набора

  live100        — 100 живых фото организаторов (62 размечены sure/likely),
                   CV: `features/qemb_base384.npz` (уже посчитан), текст:
                   `features/ocr_live_verifier.jsonl` (OCR) + `features/ocr_vlm.jsonl`
                   (VLM) — ОБА уже сохранены, НЕ пересчитываются (бриф: "на уже
                   сохранённых текстах OCR и модели").
  field_bottles  — 85 кропов бутылок с полевых снимков (part4-field-bottles.csv,
                   12 sure+likely в каталоге). Такого CV/OCR-кэша раньше не было:
                   CV-эмбеддинги считаются ЗДЕСЬ (SigLIP2 base-384, локально),
                   OCR — СВЕЖИЙ локальный RapidOCR (`scripts/ml_eng_field_ocr.py`,
                   запущен ДО этого скрипта, `case-data/ml-eng-index-20v/features/
                   ocr_field_bottles_live.jsonl`). VLM/модель-текст ЗДЕСЬ
                   НЕДОСТУПЕН офлайн (означало бы звать GPU-шлюз — бриф прямо
                   запрещает) — только CV-only и "local" (CV+OCR).
  field_frames   — 32 полных полевых кадра (part3-field.csv, 8 sure+likely),
                   симметрично field_bottles.

Для КАЖДОГО набора считаем на ОБОИХ индексах (D1 / v20-эксп.): top-1 и top-5
(только на sure+likely не-NONE подмножестве), CV-скор top-1 (среднее/медиана) и
family-gap top-1 (среднее/медиана по недоминирующим + доля доминирования) — видно
устойчивость, даже когда сам ответ не меняется (бриф п.2). Правила: `cv_only`
(голый CV-ранкинг, изолирует ИМЕННО эффект ракурсов) и `local`=CV+OCR — на всех
трёх наборах; `confident_else_cv` (боевое правило, contracts/image-scan.md,
`CV_FUSION_CHOOSE`) — только на live100 (нужен VLM-текст).

Результат — JSON-дамп `case-data/ml-eng-index-20v/compare_result.json` + таблица
в stdout; `reports/ml-eng-index-20-views.md` цитирует числа отсюда.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_CV_PKG_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_CV_PKG_DIR))

import numpy as np

from cv import families as cv_families
from cv import text_fusion

CASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
LAB = CASE / "real-photos-labels"
FEAT = LAB / "features"
EXP = CASE / "ml-eng-index-20v"
EXP_FEAT = EXP / "features"
CSV_PATH = EXP / "catalog" / "strapi_output0709.csv"  # своя копия каталога (бриф п.1)
FAMILIES_JSON = EXP / "catalog" / "families.json"
ALIASES_JSON = EXP / "catalog" / "winery_aliases.json"

D1_META = FEAT / "index_meta_base384_d1.json"
D1_VECTORS = FEAT / "index_vectors_base384_d1.npy"
V20_META = EXP_FEAT / "index_meta_v20.json"
V20_VECTORS = EXP_FEAT / "index_vectors_v20.npy"
V20_QDRANT_DIR = EXP / "index" / "qdrant"
D1_QDRANT_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/data-d1/qdrant")
D1_COPY_FOR_TIMING = EXP / "qdrant-d1-copy-mleng"  # своя копия БЕЗ .lock, только для ANN-тайминга

FIELD_FRAMES_DIR = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/Field")
FIELD_BOTTLES_DIR = LAB / "field-bottles"

# --- боевые константы слияния (сверено с infra/ams3/somelye.env.example, 22.09;
# идентичны qa/real_photos_choose_rule.py) ---
W = 0.3
COLOR_PENALTY = 0.05
OCR_UNCONFIRMED_W = 0.5
MODEL_UNCONFIRMED_W = 1.0
ANN_TOP_K = text_fusion.DEFAULT_ANN_TOP_K  # 50
TEXT_TOP_N = text_fusion.DEFAULT_TEXT_TOP_N  # 30


# ===================================================================================
# Индексы: плотные матрицы (D1 — уже дампнут ml-lead; v20 — дампим сами из Qdrant)
# ===================================================================================


def dump_qdrant_index(data_dir: Path, out_vectors: Path, out_meta: Path, *, batch: int = 2000) -> dict:
    """Сканирует embedded-Qdrant коллекцию `data_dir/qdrant` (открываем РОВНО ОДИН РАЗ,
    в этом процессе, закрываем сразу после — однопроцессный embedded-движок,
    ORCHESTRATION.md) и сохраняет плотную матрицу векторов + slugs/views в .npy/.json —
    тот же формат, что существующие features/index_vectors_base384_d1.npy +
    index_meta_base384_d1.json (qa/real_photos_choose_rule.py::load_cv())."""
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(data_dir / "qdrant"))
    try:
        collection = "cv_image_views"
        vectors: list[list[float]] = []
        slugs: list[str] = []
        views: list[str] = []
        offset = None
        while True:
            records, offset = client.scroll(
                collection_name=collection, limit=batch, offset=offset,
                with_payload=True, with_vectors=True,
            )
            for r in records:
                vectors.append(r.vector)
                slugs.append(r.payload.get("slug"))
                views.append(r.payload.get("view"))
            if offset is None:
                break
        V = np.array(vectors, dtype=np.float32)
        out_vectors.parent.mkdir(parents=True, exist_ok=True)
        np.save(out_vectors, V)
        out_meta.write_text(json.dumps({"slugs": slugs, "views": views}, ensure_ascii=False))
        return {"vectors": int(V.shape[0]), "dim": int(V.shape[1]) if V.ndim == 2 else 0, "positions": len(set(slugs))}
    finally:
        client.close()


def load_cv_matrix(meta_path: Path, vectors_path: Path, Q: dict) -> tuple[np.ndarray, list, dict]:
    """Плотная (n_photos x n_slugs) CV-матрица — max по ракурсам (на слаг) И max по
    (norm, raw) — точная мимикрия qa/real_photos_choose_rule.py::load_cv()."""
    meta = json.loads(meta_path.read_text())
    vs = meta["slugs"]
    slugs = sorted(set(vs))
    pos = {s: i for i, s in enumerate(slugs)}
    vsi = np.array([pos[s] for s in vs])
    V = np.load(vectors_path).astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    n_photos = Q["norm"].shape[0]
    CV = np.full((n_photos, len(slugs)), -1.0, dtype=np.float32)
    for k in ("norm", "raw"):
        q = Q[k].astype(np.float32)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        S = q @ V.T
        tmp = np.full((n_photos, len(slugs)), -1.0, dtype=np.float32)
        for i in range(n_photos):
            row = np.full(len(slugs), -1.0, dtype=np.float32)
            np.maximum.at(row, vsi, S[i])
            tmp[i] = row
        CV = np.maximum(CV, tmp)
    return CV, slugs, pos


# ===================================================================================
# Query-эмбеддинги (СВЕЖИЕ, локально — только для field_bottles/field_frames)
# ===================================================================================


def compute_qemb(photo_paths: list[Path], *, model: str = "google/siglip2-base-patch16-384") -> dict:
    """N=2 query-вектора (норм. кроп этикетки + весь кадр) на фото — точная мимикрия
    `cv.index.ImageIndex.embed_fusion_query()` (CV_FUSION_CROPS=2, боевой дефолт
    стенда), но без ImageIndex/Store целиком (не открываем Qdrant ради этого) —
    напрямую энкодер + normalize_query. ЛОКАЛЬНО (MPS/CPU) — не GPU-шлюз."""
    from cv.encoder import SiglipEncoder
    from cv.normalize import normalize_query
    from cv import imageio

    enc = SiglipEncoder(model_name=model)
    norm_list, raw_list = [], []
    t0 = time.perf_counter()
    for i, p in enumerate(photo_paths, 1):
        arr = imageio.load_image_file(str(p))
        crops = [normalize_query(arr, enabled=True), arr]
        vecs = enc.encode_batch(crops, use_cache=True)
        norm_list.append(vecs[0])
        raw_list.append(vecs[1])
        if i % 20 == 0 or i == len(photo_paths):
            print(f"[qemb] {i}/{len(photo_paths)} за {time.perf_counter()-t0:.1f}с (device={enc.device})", file=sys.stderr)
    return {"norm": np.array(norm_list, dtype=np.float32), "raw": np.array(raw_list, dtype=np.float32)}


# ===================================================================================
# Наборы данных
# ===================================================================================


def load_live100():
    photos = json.loads((FEAT / "photos.json").read_text())
    idx_of = {p: i for i, p in enumerate(photos)}
    labels = {}
    for f in sorted(LAB.glob("part[12].csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                labels[r["photo"]] = r["true_slug"]
    in_cat = [p for p in photos if labels[p] not in ("", "NONE")]
    Q = np.load(FEAT / "qemb_base384.npz")
    Qd = {"norm": Q["norm"], "raw": Q["raw"]}
    ocr_text = {json.loads(l)["photo"]: json.loads(l)["text"]
                for l in (FEAT / "ocr_live_verifier.jsonl").read_text().splitlines() if l.strip()}
    vlm_text = {json.loads(l)["photo"]: json.loads(l)["text"]
                for l in (FEAT / "ocr_vlm.jsonl").read_text().splitlines() if l.strip()}
    ocr_text = {p: ocr_text.get(p, "") for p in photos}
    vlm_text = {p: vlm_text.get(p, "") for p in photos}
    return {
        "name": "live100", "photos": photos, "idx_of": idx_of, "labels": labels,
        "in_cat": in_cat, "Q": Qd, "ocr_text": ocr_text, "vlm_text": vlm_text,
    }


def _load_field_set(tag: str, csv_path: Path, images_dir: Path, ocr_jsonl: Path):
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    photos = [r["photo"] for r in rows]
    labels = {r["photo"]: r["true_slug"] for r in rows}
    confidence = {r["photo"]: r["confidence"] for r in rows}
    idx_of = {p: i for i, p in enumerate(photos)}
    in_cat = [p for p in photos if labels[p] not in ("", "NONE") and confidence[p] in ("sure", "likely")]
    unsure_in_cat = [p for p in photos if labels[p] not in ("", "NONE") and confidence[p] == "unsure"]
    ocr_text = {json.loads(l)["photo"]: json.loads(l)["text"]
                for l in ocr_jsonl.read_text().splitlines() if l.strip()}
    ocr_text = {p: ocr_text.get(p, "") for p in photos}
    qemb_path = EXP_FEAT / f"qemb_{tag}.npz"
    if qemb_path.exists():
        Qz = np.load(qemb_path)
        Q = {"norm": Qz["norm"], "raw": Qz["raw"]}
    else:
        print(f"[{tag}] считаю query-эмбеддинги свежо (SigLIP2 base-384, локально)…", file=sys.stderr)
        Q = compute_qemb([images_dir / p for p in photos])
        EXP_FEAT.mkdir(parents=True, exist_ok=True)
        np.savez(qemb_path, norm=Q["norm"], raw=Q["raw"])
    return {
        "name": tag, "photos": photos, "idx_of": idx_of, "labels": labels,
        "in_cat": in_cat, "unsure_in_cat": unsure_in_cat, "Q": Q, "ocr_text": ocr_text, "vlm_text": None,
    }


def load_field_bottles():
    return _load_field_set(
        "field_bottles", LAB / "part4-field-bottles.csv", FIELD_BOTTLES_DIR,
        EXP_FEAT / "ocr_field_bottles_live.jsonl",
    )


def load_field_frames():
    return _load_field_set(
        "field_frames", LAB / "part3-field.csv", FIELD_FRAMES_DIR,
        EXP_FEAT / "ocr_field_frames_live.jsonl",
    )


# ===================================================================================
# Слияние — боевая формула (cv/text_fusion.py), мимикрия qa/real_photos_choose_rule.py
# ===================================================================================


class Fuser:
    def __init__(self, CV, slugs, pos):
        self.CV, self.slugs, self.pos = CV, slugs, pos
        self.text_index = text_fusion.load_catalog_index(str(CSV_PATH))
        self.winery_index = text_fusion.load_winery_index(str(CSV_PATH), aliases_json=str(ALIASES_JSON))
        self.colors = text_fusion.color_by_slug(self.text_index)
        self.fam = cv_families.load_family_by_slug(FAMILIES_JSON)

    def _cv_scores(self, i, text_top_slugs):
        row = self.CV[i]
        order = np.argsort(-row)[:ANN_TOP_K]
        out = {self.slugs[j]: float(row[j]) for j in order}
        for s in text_top_slugs:
            if s in self.pos and s not in out:
                out[s] = float(row[self.pos[s]])
        return out

    def fuse(self, photo_idx: int, text: str, *, unconfirmed_w: float) -> text_fusion.FusionResult:
        _, mass = self.text_index.scores(text)
        text_top = text_fusion.top_text_slugs(self.text_index, mass, TEXT_TOP_N)
        return text_fusion.fuse(
            self._cv_scores(photo_idx, text_top), self.text_index, text, family_by_slug=self.fam, w=W,
            winery_index=self.winery_index, unconfirmed_winery_w=unconfirmed_w,
            colors=self.colors, color_penalty=COLOR_PENALTY,
        )

    def cv_only(self, i):
        """Голый CV-ранкинг (text="" -> rel=0 для всех -> final==cv_score, тот же
        family-gap на final_score == family-gap на голом CV) — изолирует ИМЕННО
        эффект ракурсов индекса, без примеси текста. Через ТУ ЖЕ боевую fuse(),
        не отдельная функция — см. докстринг модуля."""
        return self.fuse(i, "", unconfirmed_w=1.0)

    def local(self, i, ocr_text):
        return self.fuse(i, ocr_text, unconfirmed_w=OCR_UNCONFIRMED_W)

    def model(self, i, vlm_text):
        return self.fuse(i, vlm_text, unconfirmed_w=MODEL_UNCONFIRMED_W)


def top1(r: "text_fusion.FusionResult") -> str | None:
    return r.ranked[0].slug if r.ranked else None


def top5(r: "text_fusion.FusionResult") -> list:
    return [c.slug for c in r.ranked[:5]]


# ===================================================================================
# Метрики: top1/top5 + CV-скор top1 + family-gap top1, на sure+likely подмножестве
# ===================================================================================


def summarize(results: dict, labels: dict, in_cat: list) -> dict:
    """results: photo -> FusionResult. Возвращает top1/top5 (доля и счёт), CV-скор
    top1 (среднее/медиана) и family-gap top1 (среднее/медиана по недоминирующим +
    доля доминирования), все — на подмножестве `in_cat`."""
    n = len(in_cat)
    hit1 = sum(1 for p in in_cat if top1(results[p]) == labels[p])
    hit5 = sum(1 for p in in_cat if labels[p] in top5(results[p]))
    cv_scores = [results[p].ranked[0].cv_score for p in in_cat if results[p].ranked]
    gaps = [results[p].gap for p in in_cat if results[p].ranked]
    finite_gaps = [g for g in gaps if g is not None]
    dominant = sum(1 for g in gaps if g is None)
    return {
        "n": n,
        "top1": {"hit": hit1, "acc": round(hit1 / n, 4) if n else None},
        "top5": {"hit": hit5, "acc": round(hit5 / n, 4) if n else None},
        "cv_score_top1": {
            "mean": round(statistics.fmean(cv_scores), 4) if cv_scores else None,
            "median": round(statistics.median(cv_scores), 4) if cv_scores else None,
        },
        "family_gap_top1": {
            "mean_finite": round(statistics.fmean(finite_gaps), 4) if finite_gaps else None,
            "median_finite": round(statistics.median(finite_gaps), 4) if finite_gaps else None,
            "dominant_frac": round(dominant / n, 4) if n else None,
        },
    }


def eval_dataset(ds: dict, fs: Fuser) -> dict:
    in_cat = ds["in_cat"]
    idx_of, labels, ocr_text, vlm_text = ds["idx_of"], ds["labels"], ds["ocr_text"], ds["vlm_text"]

    cv_only_r = {p: fs.cv_only(idx_of[p]) for p in in_cat}
    local_r = {p: fs.local(idx_of[p], ocr_text[p]) for p in in_cat}
    out = {
        "cv_only": summarize(cv_only_r, labels, in_cat),
        "local_cv_ocr": summarize(local_r, labels, in_cat),
    }
    if vlm_text is not None:
        model_r = {p: fs.model(idx_of[p], vlm_text[p]) for p in in_cat}
        confident_r = {}
        for p in in_cat:
            m = model_r[p]
            confident_r[p] = m if (vlm_text[p].strip() and m.confident) else local_r[p]
        out["model_cv_vlm"] = summarize(model_r, labels, in_cat)
        out["confident_else_cv"] = summarize(confident_r, labels, in_cat)
    return out


# ===================================================================================
# Цена: размер индекса / ANN-тайминг
# ===================================================================================


def dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def bench_ann_search(qdrant_dir: Path, query_vectors: np.ndarray, *, top_k: int = 50, n: int = 60) -> dict:
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(qdrant_dir))
    try:
        collection = "cv_image_views"
        timings = []
        for i in range(n):
            v = query_vectors[i % len(query_vectors)].tolist()
            t0 = time.perf_counter()
            client.query_points(collection_name=collection, query=v, limit=top_k, with_payload=True)
            timings.append((time.perf_counter() - t0) * 1000)
        arr = np.array(timings)
        return {
            "n": n, "top_k": top_k,
            "p50_ms": round(float(np.percentile(arr, 50)), 2),
            "p95_ms": round(float(np.percentile(arr, 95)), 2),
            "mean_ms": round(float(arr.mean()), 2),
            "max_ms": round(float(arr.max()), 2),
        }
    finally:
        client.close()


def make_d1_copy_without_lock() -> Path:
    """Копия D1_QDRANT_DIR (`packages/cv/data-d1/qdrant`) БЕЗ `.lock`, на ОДНОМ уровне
    вложенности (сама возвращаемая директория — то, что нужно передавать в
    `QdrantClient(path=...)`/`bench_ann_search()` напрямую, БЕЗ дополнительного
    "/qdrant" — та же ошибка на этом уровне однажды уже дала "Collection not found":
    `QdrantClient` открывал ПУСТОЙ новый стор на директорию-родителя и молча
    инициализировал там свежие `.lock`/`meta.json`, не находя реальных данных
    уровнем глубже)."""
    import shutil

    if D1_COPY_FOR_TIMING.exists():
        shutil.rmtree(D1_COPY_FOR_TIMING)
    D1_COPY_FOR_TIMING.mkdir(parents=True)
    for item in D1_QDRANT_DIR.iterdir():
        if item.name == ".lock":
            continue
        dest = D1_COPY_FOR_TIMING / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    return D1_COPY_FOR_TIMING


# ===================================================================================
# main
# ===================================================================================


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-v20", action="store_true", help="дампнуть векторы v20 из Qdrant в .npy/.json")
    ap.add_argument("--ann-timing", action="store_true", help="измерить ANN-поиск на D1 (своя копия без .lock) и v20")
    ap.add_argument("--out", default=str(EXP / "compare_result.json"))
    a = ap.parse_args()

    result: dict = {}

    if a.dump_v20:
        print("[dump-v20] сканирую Qdrant v20…", file=sys.stderr)
        info = dump_qdrant_index(EXP / "index", V20_VECTORS, V20_META)
        print(f"[dump-v20] {info}", file=sys.stderr)
        result["v20_dump"] = info

    print("[load] наборы данных…", file=sys.stderr)
    live100 = load_live100()
    field_bottles = load_field_bottles()
    field_frames = load_field_frames()
    datasets = [live100, field_bottles, field_frames]
    for ds in datasets:
        print(f"  {ds['name']}: {len(ds['photos'])} фото, in_cat(sure+likely)={len(ds['in_cat'])}", file=sys.stderr)

    print("[cv] строю CV-матрицы D1 / v20…", file=sys.stderr)
    per_index = {}
    for tag, meta_p, vec_p in (("d1", D1_META, D1_VECTORS), ("v20", V20_META, V20_VECTORS)):
        per_ds = {}
        for ds in datasets:
            CV, slugs, pos = load_cv_matrix(meta_p, vec_p, ds["Q"])
            fs = Fuser(CV, slugs, pos)
            per_ds[ds["name"]] = eval_dataset(ds, fs)
        per_index[tag] = per_ds

    result["metrics"] = per_index

    if a.ann_timing:
        # limit=400 — РЕАЛЬНЫЙ боевой оверфетч на один query-вектор (cv/index.py::
        # search_fusion(): overfetch = max(top_k*SEARCH_OVERFETCH(8), top_k+10) при
        # top_k=50, ровно ANN_TOP_K выше), не голый top_k=50 — иначе цифра занижает
        # реальную боевую стоимость запроса (production делает N=2 таких вызова на
        # скан, по одному на кроп CV_FUSION_CROPS).
        overfetch = max(ANN_TOP_K * 8, ANN_TOP_K + 10)
        print("[ann-timing] бенч D1 (своя копия без .lock)…", file=sys.stderr)
        d1_copy = make_d1_copy_without_lock()
        d1_timing = bench_ann_search(d1_copy, live100["Q"]["norm"], top_k=overfetch)
        print(f"[ann-timing] D1: {d1_timing}", file=sys.stderr)
        print("[ann-timing] бенч v20…", file=sys.stderr)
        v20_timing = bench_ann_search(V20_QDRANT_DIR, live100["Q"]["norm"], top_k=overfetch)
        print(f"[ann-timing] v20: {v20_timing}", file=sys.stderr)
        result["ann_timing"] = {"d1": d1_timing, "v20": v20_timing}
        result["disk_mb"] = {"d1": dir_size_mb(D1_QDRANT_DIR), "v20": dir_size_mb(V20_QDRANT_DIR)}

    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n[main] сохранено: {a.out}")

    for tag in ("d1", "v20"):
        print(f"\n=== индекс {tag} ===")
        for ds_name, rules in result["metrics"][tag].items():
            for rule_name, m in rules.items():
                print(f"  {ds_name:14s} {rule_name:18s} top1={m['top1']['hit']}/{m['n']} "
                      f"top5={m['top5']['hit']}/{m['n']} cv_top1_mean={m['cv_score_top1']['mean']} "
                      f"gap_mean_finite={m['family_gap_top1']['mean_finite']} dominant={m['family_gap_top1']['dominant_frac']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
