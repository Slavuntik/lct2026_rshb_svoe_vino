"""Ingest каталога vines в Qdrant (embedded) + sparse BM25 + labels-корпус.

Источники (read-only):
  build/index.jsonl     -> коллекция "wines"
  build/wineries.jsonl  -> коллекция "wineries"
  build/articles.jsonl  -> коллекция "knowledge"
  catalog/wines/<slug>.json     -> обогащение карточек вина (derived.*, source.*)
  catalog/wineries/<slug>.json  -> обогащение карточек винодельни

Идемпотентность: id каждой записи стабилен (wine slug / "winery:<slug>" /
"article:<id>#<n>"), точки в Qdrant адресуются uuid5(id) — повторный upsert
того же id перезаписывает точку, а не дублирует. Сайдкары (payloads/*.jsonl,
bm25/*.pkl, labels.jsonl) перезаписываются целиком на каждом прогоне.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag import config, refdata
from rag.calibrate import calibrate_refusal_threshold, load_goldset_safe
from rag.embeddings import BM25Index, DenseEmbedder
from rag.hybrid import HybridSearcher
from rag.rerank import build_reranker
from rag.store import QdrantStore


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_catalog_card(catalog_dir: Path, subdir: str, slug: str) -> dict | None:
    path = catalog_dir / subdir / f"{slug}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class SourceRecord:
    id: str
    kind: str
    text: str
    url: str
    payload: dict[str, Any]


def build_wine_records(build_dir: Path, catalog_dir: Path) -> list[SourceRecord]:
    rows = _read_jsonl(build_dir / "index.jsonl")
    out = []
    for row in rows:
        slug = row["id"]
        card = _load_catalog_card(catalog_dir, "wines", slug) or {}
        source = card.get("source", {})
        derived = card.get("derived", {})

        filt = dict(row.get("filters", {}))
        filt["region"] = refdata.normalize_region(filt.get("region"))
        # v0.2: payload = filters-блок index.jsonl ПЛЮС stillness и
        # reference_style_matches из derived (в исходном filters-блоке их нет).
        filt["stillness"] = derived.get("stillness")
        filt["reference_style_matches"] = derived.get("reference_style_matches") or row.get("styles") or []

        payload = {
            # "filters"/"sensory" — ВНУТРЕННЕЕ представление (жёсткая фильтрация до
            # векторов, ранжирование analog_for_style/candidates_for_taste). В
            # Candidate.meta наружу не идут как есть — см. rag/meta.py::public_meta.
            "filters": filt,
            "sensory": row.get("sensory", {}),
            # "source"/"derived" — блоки карточки vines ЦЕЛИКОМ, форма
            # Candidate.meta для kind=wine зафиксирована контрактом v0.3
            # (ревью 02, блокер 1): {"source": {...}, "derived": {...}}.
            "source": source,
            "derived": derived,
        }
        out.append(SourceRecord(id=slug, kind="wine", text=row["text"], url=row["url"], payload=payload))
    return out


def build_winery_records(build_dir: Path, catalog_dir: Path) -> list[SourceRecord]:
    rows = _read_jsonl(build_dir / "wineries.jsonl")
    out = []
    for row in rows:
        slug = row["id"]
        card = _load_catalog_card(catalog_dir, "wineries", slug) or {}
        source = card.get("source", {})

        filt = dict(row.get("filters", {}))
        region_name = filt.get("region")
        filt["region_name"] = region_name
        filt["region"] = refdata.normalize_region(region_name)

        payload = {
            "filters": filt,  # внутреннее — региональная фильтрация до векторов
            "wine_slugs": row.get("wine_slugs", []),
            # kind=winery: Candidate.meta = {"source": {...}} (контракт v0.3).
            # У winery-карточек vines нет derived-блока (в отличие от вин).
            "source": {**source, "name": source.get("name") or slug},
        }
        out.append(
            SourceRecord(id=f"winery:{slug}", kind="winery", text=row["text"], url=row["url"], payload=payload)
        )
    return out


def build_knowledge_records(build_dir: Path) -> list[SourceRecord]:
    rows = _read_jsonl(build_dir / "articles.jsonl")
    out = []
    for row in rows:
        payload = {
            "filters": dict(row.get("filters", {})),
            "title": row.get("title"),
            "heading": row.get("heading"),
            "article_id": row.get("article_id"),
        }
        out.append(
            SourceRecord(
                id=f"article:{row['id']}", kind="chunk", text=row["text"], url=row["url"], payload=payload
            )
        )
    return out


def build_labels(wine_records: list[SourceRecord]) -> list[dict]:
    """Корпус для resolve_label: name + winery_name + синонимы сортов вина."""
    syn_map = refdata.grape_slug_to_synonyms()
    labels = []
    for rec in wine_records:
        p = rec.payload
        grape_slugs = p.get("filters", {}).get("grapes") or []
        synonyms: list[str] = []
        for slug in grape_slugs:
            synonyms.extend(syn_map.get(slug, []))
        source = p.get("source") or {}
        name = source.get("name") or ""
        winery_name = source.get("winery_name") or ""
        search_text = " ".join([name, winery_name, *synonyms]).strip()
        labels.append(
            {
                "id": rec.id,
                "name": name,
                "winery_name": winery_name,
                "search_text": search_text,
                "text": rec.text,
                "url": rec.url,
            }
        )
    return labels


def run_ingest(
    version: str | None = None,
    source_dir: Path | None = None,
    catalog_dir: Path | None = None,
    data_dir: Path | None = None,
    embedder: DenseEmbedder | None = None,
    goldset_path: Path | None = None,
) -> dict:
    t_start = time.perf_counter()
    build_dir = source_dir or config.BUILD_DIR
    cat_dir = catalog_dir or config.CATALOG_DIR
    ddir = data_dir or config.DATA_DIR
    payloads_dir = ddir / "payloads"
    bm25_dir = ddir / "bm25"
    labels_path = ddir / "labels.jsonl"
    manifest_path = ddir / "manifest.json"

    version = version or datetime.now(timezone.utc).strftime("%Y%m%d.1")
    embedder = embedder or DenseEmbedder()
    store = QdrantStore(path=ddir / "qdrant")

    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    collections = {
        "wines": build_wine_records(build_dir, cat_dir),
        "wineries": build_winery_records(build_dir, cat_dir),
        "knowledge": build_knowledge_records(build_dir),
    }
    timings["read_source_s"] = time.perf_counter() - t0

    payloads_dir.mkdir(parents=True, exist_ok=True)
    bm25_dir.mkdir(parents=True, exist_ok=True)

    counts = {}
    t_embed_total = 0.0
    t_upsert_total = 0.0
    bm25_indexes: dict[str, BM25Index] = {}
    payload_by_id: dict[str, dict[str, dict]] = {}
    for name, records in collections.items():
        ids = [r.id for r in records]
        texts = [r.text for r in records]

        t0 = time.perf_counter()
        vectors = embedder.embed_documents(texts) if texts else []
        t_embed_total += time.perf_counter() - t0

        dim = len(vectors[0]) if vectors else config.DENSE_DIM
        store.ensure_collection(name, dim)

        t0 = time.perf_counter()
        payload_dicts = [
            {"kind": r.kind, "text": r.text, "url": r.url, **r.payload} for r in records
        ]
        if ids:
            store.upsert(name, ids, vectors, payload_dicts)
        t_upsert_total += time.perf_counter() - t0

        # sidecar payloads (единый источник для BM25-фильтрации/resolve/analog —
        # не требует повторного похода в Qdrant или в catalog/ на чтении).
        id_payload_pairs = [{"id": sid, **pd} for sid, pd in zip(ids, payload_dicts)]
        with open(payloads_dir / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for row in id_payload_pairs:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        payload_by_id[name] = {row["id"]: row for row in id_payload_pairs}

        bm25 = BM25Index.build(ids, texts)
        bm25.save(bm25_dir / f"{name}.pkl")
        bm25_indexes[name] = bm25

        counts[name] = len(records)

    timings["embed_s"] = t_embed_total
    timings["upsert_s"] = t_upsert_total

    labels = build_labels(collections["wines"])
    with open(labels_path, "w", encoding="utf-8") as f:
        for lab in labels:
            f.write(json.dumps(lab, ensure_ascii=False) + "\n")

    # Калибровка порога refusal (контракт v0.3, ревью 02, блокер-риск 2):
    # нужен РАБОЧИЙ HybridSearcher поверх только что построенного индекса
    # (Qdrant-точки/BM25/payload уже на месте — манифест ещё не записан, но
    # он calibrate'у и не нужен). Голд-сет опционален: на синтетических
    # тестовых фикстурах (без eval/goldset.jsonl) калибровка тихо
    # пропускается, refusal остаётся выключенным (threshold=None) — ingest
    # не должен падать без голд-сета.
    t0 = time.perf_counter()
    gpath = goldset_path or config.DEFAULT_GOLDSET_PATH
    goldset = load_goldset_safe(gpath)
    refusal_calibration: dict = {"threshold": None, "reason": "no_goldset", "goldset_path": str(gpath)}
    if goldset:
        calib_reranker = build_reranker()
        calib_hybrid = HybridSearcher(
            store=store,
            embedder=embedder,
            bm25_indexes=bm25_indexes,
            payload_by_id=payload_by_id,
            reranker=calib_reranker,
        )
        refusal_calibration = calibrate_refusal_threshold(calib_hybrid, goldset, top_k=8)
        refusal_calibration["goldset_path"] = str(gpath)
    timings["calibrate_refusal_s"] = time.perf_counter() - t0

    timings["total_s"] = time.perf_counter() - t_start

    manifest = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dense_model": embedder.model_name,
        "dense_dim": len(vectors[0]) if vectors else config.DENSE_DIM,
        "reranker": config.RERANKER_MODEL_NAME or None,
        "counts": counts,
        "timings": timings,
        "refusal_threshold": refusal_calibration.get("threshold"),
        "refusal_calibration": refusal_calibration,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    store.close()
    return manifest


def get_index_version(data_dir: Path | None = None) -> str | None:
    ddir = data_dir or config.DATA_DIR
    manifest_path = ddir / "manifest.json"
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f).get("version")
