"""Кэш признаков по реальным фото кейса — для офлайн-оценки схем слияния CV + текст.

Что считает (всё в case-data/real-photos-labels/features/, вне git):
  slugs.json            — порядок слагов в матрицах близости
  sims_norm.npy         — [N_фото x N_слагов] max-косинус по ракурсам, запрос через normalize_query()
  sims_raw.npy          — то же без нормализации (весь кадр в энкодер)
  ocr_<вариант>.jsonl   — {photo, text, ms} для вариантов OCR (см. VARIANTS)

Запуск: CV-venv, из packages/cv:
  .venv/bin/python ../../qa/real_photos_features.py [--ocr crop320,crop640,full960] [--no-sims]
Инкрементально: готовые фото в ocr_*.jsonl пропускаются.
"""
import argparse, json, time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from cv import imageio
from cv.index import ImageIndex
from cv.normalize import normalize_query
from cv.verify import LabelVerifier

BASE = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
SRC = BASE / "real-photos"
OUT = BASE / "real-photos-labels" / "features"

# вариант -> (источник кадра, сторона для OCR)
VARIANTS = {
    "crop320": ("norm", 320),   # как в бою сейчас
    "crop640": ("norm", 640),
    "full640": ("full", 640),
    "full960": ("full", 960),
    "full1280": ("full", 1280),
    # центральный кроп бутылки (тот же, что уходит VLM: доли 0.15/0.05/0.85/0.98) — без детектора
    "cwide640": ("cwide", 640),
    "cwide800": ("cwide", 800),
    "cwide1024": ("cwide", 1024),
}
CWIDE = (0.15, 0.05, 0.85, 0.98)


def load_full(path: Path) -> np.ndarray:
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(im)


def export_vectors(idx: ImageIndex):
    client = idx.store.client
    vecs, slugs, offset = [], [], None
    while True:
        pts, offset = client.scroll(idx.collection, limit=5000, offset=offset, with_vectors=True, with_payload=True)
        for p in pts:
            vecs.append(np.asarray(p.vector, dtype=np.float32))
            slugs.append(p.payload.get("slug"))
        if offset is None:
            break
    V = np.stack(vecs)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    return V, np.array(slugs)


def per_slug_max(sim_views: np.ndarray, view_slug_idx: np.ndarray, n_slugs: int) -> np.ndarray:
    out = np.full(n_slugs, -1.0, dtype=np.float32)
    np.maximum.at(out, view_slug_idx, sim_views)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", default="crop320,crop640,full960")
    ap.add_argument("--no-sims", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    photos = sorted(SRC.glob("*.webp"))
    if a.limit:
        photos = photos[: a.limit]

    if not a.no_sims:
        idx = ImageIndex()
        print("индекс:", idx.index_version, flush=True)
        V, view_slugs = export_vectors(idx)
        uniq = sorted(set(view_slugs.tolist()))
        pos = {s: i for i, s in enumerate(uniq)}
        vsi = np.array([pos[s] for s in view_slugs])
        print(f"векторов {len(V)}, слагов {len(uniq)}", flush=True)
        (OUT / "slugs.json").write_text(json.dumps(uniq, ensure_ascii=False))
        S_norm, S_raw = [], []
        for i, p in enumerate(photos, 1):
            arr = imageio.decode_image(p.read_bytes())
            for S, prepared in ((S_norm, normalize_query(arr, enabled=True)), (S_raw, arr)):
                q = np.asarray(idx.encoder.encode(prepared), dtype=np.float32)
                q /= np.linalg.norm(q)
                S.append(per_slug_max(V @ q, vsi, len(uniq)))
            if i % 20 == 0:
                print(f"эмбеддинги {i}/{len(photos)}", flush=True)
        np.save(OUT / "sims_norm.npy", np.stack(S_norm))
        np.save(OUT / "sims_raw.npy", np.stack(S_raw))
        (OUT / "photos.json").write_text(json.dumps([p.name for p in photos], ensure_ascii=False))
        idx.store.close()

    for var in [v for v in a.ocr.split(",") if v]:
        src, size = VARIANTS[var]
        path = OUT / f"ocr_{var}.jsonl"
        done = set()
        if path.exists():
            done = {json.loads(l)["photo"] for l in path.read_text().splitlines() if l.strip()}
        ver = LabelVerifier(ocr_size=size)
        ver.ocr_size = size  # env CV_OCR_SIZE не должен перебивать вариант
        with path.open("a", encoding="utf-8") as fh:
            for i, p in enumerate(photos, 1):
                if p.name in done:
                    continue
                if src == "norm":
                    arr = normalize_query(imageio.decode_image(p.read_bytes()), enabled=True)
                elif src == "cwide":
                    full = load_full(p)
                    h, w = full.shape[:2]
                    x0, y0, x1, y1 = CWIDE
                    arr = np.ascontiguousarray(full[int(h * y0):int(h * y1), int(w * x0):int(w * x1)])
                else:
                    arr = load_full(p)
                t = time.time()
                text = ver.read_text(arr)
                ms = round((time.time() - t) * 1000)
                fh.write(json.dumps({"photo": p.name, "text": text, "ms": ms}, ensure_ascii=False) + "\n")
                fh.flush()
                if i % 20 == 0:
                    print(f"OCR {var} {i}/{len(photos)}", flush=True)
        print(f"OCR {var} готово", flush=True)


if __name__ == "__main__":
    main()
