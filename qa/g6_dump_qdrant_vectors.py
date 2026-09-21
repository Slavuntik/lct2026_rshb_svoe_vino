#!/usr/bin/env python3
"""qa/g6_dump_qdrant_vectors.py — G6 (доп. задание оркестратора 21.09): выгрузка
векторов+метаданных ЭКСПЕРИМЕНТАЛЬНОГО qdrant-индекса кандидата (data-exp/<model>,
свой CV_DATA_DIR — не боевой, открывать/скроллить напрямую безопасно, никакого
чужого лока) в тот же формат, что `case-data/real-photos-labels/features/
index_vectors.npy` + `index_meta.json` (боевой base-224, выгружен отдельно D1) —
для сравнения энкодеров на реальных фото через qa/real_photos_views.py (--vectors/
--meta).

Запуск (venv пакета cv):
    packages/cv/.venv/bin/python qa/g6_dump_qdrant_vectors.py \\
        --data-dir packages/cv/data-exp/siglip2-base-384-v6 \\
        --out-vectors .../features/index_vectors_base384.npy \\
        --out-meta .../features/index_meta_base384.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="CV_DATA_DIR построенного индекса (свой, не packages/cv/data)")
    parser.add_argument("--collection", default="cv_image_views")
    parser.add_argument("--out-vectors", required=True)
    parser.add_argument("--out-meta", required=True)
    args = parser.parse_args(argv)

    from qdrant_client import QdrantClient

    qdrant_path = Path(args.data_dir) / "qdrant"
    client = QdrantClient(path=str(qdrant_path))
    slugs: list[str] = []
    views: list[str] = []
    vecs: list[np.ndarray] = []
    offset = None
    n_batches = 0
    try:
        while True:
            points, offset = client.scroll(
                collection_name=args.collection, with_vectors=True, with_payload=True, limit=1000, offset=offset
            )
            for p in points:
                payload = p.payload or {}
                slugs.append(payload.get("slug"))
                views.append(payload.get("view"))
                v = np.asarray(p.vector, dtype=np.float32)
                norm = np.linalg.norm(v)
                vecs.append(v / norm if norm > 0 else v)
            n_batches += 1
            if offset is None:
                break
    finally:
        client.close()

    V = np.stack(vecs) if vecs else np.zeros((0, 0), dtype=np.float32)
    Path(args.out_vectors).parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out_vectors, V)
    Path(args.out_meta).write_text(json.dumps({"slugs": slugs, "views": views}, ensure_ascii=False), encoding="utf-8")
    print(f"[dump] {V.shape[0]} векторов (dim={V.shape[1] if V.size else 0}, {n_batches} батчей scroll) -> {args.out_vectors} / {args.out_meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
