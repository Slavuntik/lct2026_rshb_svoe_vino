"""CLI: `rag ingest` и `rag eval` — по одной команде каждая (DoD)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag import config
from rag.base import Retriever
from rag.eval import benchmark_latency, evaluate, load_goldset
from rag.ingest import run_ingest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDSET = PACKAGE_ROOT / "eval" / "goldset.jsonl"
DEFAULT_REPORT = PACKAGE_ROOT / "eval" / "report.json"


def cmd_ingest(args: argparse.Namespace) -> int:
    manifest = run_ingest(
        version=args.version,
        source_dir=Path(args.source) if args.source else None,
        catalog_dir=Path(args.catalog) if args.catalog else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    goldset_path = Path(args.goldset)
    goldset = load_goldset(goldset_path)
    if not goldset:
        print(f"Голд-сет пуст или не найден: {goldset_path}", file=sys.stderr)
        return 1

    retriever = Retriever()
    report = evaluate(retriever, goldset, top_k=args.top_k)

    queries = [q["q"] for q in goldset]
    report["latency_ms"] = {
        "without_reranker": benchmark_latency(
            retriever, queries, n=args.bench_n, use_reranker=False, top_k=args.top_k
        ),
        "with_reranker": benchmark_latency(
            retriever, queries, n=args.bench_n, use_reranker=True, top_k=args.top_k
        ),
    }
    report["index_version"] = retriever.index_version

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    summary = {k: v for k, v in report.items() if k != "details"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nПолный отчёт (с деталями по каждому вопросу): {out_path}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag", description="RAG-ядро «Свой Сомелье»")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Индексация каталога vines: Qdrant + BM25 + labels")
    p_ingest.add_argument("--source", default=None, help=f"build_dir vines (default: {config.BUILD_DIR})")
    p_ingest.add_argument("--catalog", default=None, help=f"catalog_dir vines (default: {config.CATALOG_DIR})")
    p_ingest.add_argument("--version", default=None, help="YYYYMMDD.N (default: сегодняшняя дата)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_eval = sub.add_parser("eval", help="Прогон голд-сета: hit@k, MRR, латентность p95")
    p_eval.add_argument("--goldset", default=str(DEFAULT_GOLDSET))
    p_eval.add_argument("--out", default=str(DEFAULT_REPORT))
    p_eval.add_argument("--top-k", type=int, default=8)
    p_eval.add_argument("--bench-n", type=int, default=100, help="Число запросов для замера p95")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
