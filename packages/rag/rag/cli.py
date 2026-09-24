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
DEFAULT_GOLDSET = config.DEFAULT_GOLDSET_PATH
DEFAULT_REPORT = PACKAGE_ROOT / "eval" / "report.json"


def cmd_ingest(args: argparse.Namespace) -> int:
    manifest = run_ingest(
        version=args.version,
        source_dir=Path(args.source) if args.source else None,
        catalog_dir=Path(args.catalog) if args.catalog else None,
        data_dir=Path(args.data) if args.data else None,
        goldset_path=Path(args.goldset) if args.goldset else None,
        case_data_dir=Path(args.case_data) if args.case_data else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    goldset_path = Path(args.goldset)
    goldset = load_goldset(goldset_path)
    if not goldset:
        print(f"Голд-сет пуст или не найден: {goldset_path}", file=sys.stderr)
        return 1

    retriever = Retriever(data_dir=Path(args.data) if args.data else None)
    # Хедлайн — routing="heuristic": как реально позовёт прод (/chat вызывает
    # search(q) без явных collections, эвристика rag/intent.py решает сама,
    # контракт v0.3 п.3). routing="oracle" — потолок retrieval-ядра при
    # коллекциях, известных из разметки голд-сета (недостижимо в проде,
    # но полезно для диагностики: разрыв heuristic/oracle = цена эвристики).
    report = evaluate(retriever, goldset, top_k=args.top_k, routing="heuristic")
    oracle_report = evaluate(retriever, goldset, top_k=args.top_k, routing="oracle")
    report["oracle_comparison"] = {k: v for k, v in oracle_report.items() if k != "details"}

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
    p_ingest.add_argument(
        "--data", default=None, help=f"куда писать индекс (default: {config.DATA_DIR}) — reports/backend-rag-rebuild.md: "
        "используйте для сборки ВНЕ packages/rag/data, например в case-data/, не трогая боевой индекс"
    )
    p_ingest.add_argument("--version", default=None, help="YYYYMMDD.N (default: сегодняшняя дата)")
    p_ingest.add_argument(
        "--goldset",
        default=None,
        help=f"голд-сет для калибровки refusal-порога (default: {config.DEFAULT_GOLDSET_PATH}; "
        "пусто/нет файла -> калибровка пропускается, refusal выключен)",
    )
    p_ingest.add_argument(
        "--case-data",
        default=None,
        help=f"каталог кейса-сканера, дополняет 'wines' вне source/index.jsonl (default: {config.CASE_DATA_DIR}; "
        "нет case_catalog.json на машине -> дополнение молча пропускается)",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_eval = sub.add_parser("eval", help="Прогон голд-сета: hit@k, MRR, латентность p95")
    p_eval.add_argument("--goldset", default=str(DEFAULT_GOLDSET))
    p_eval.add_argument("--out", default=str(DEFAULT_REPORT))
    p_eval.add_argument("--top-k", type=int, default=8)
    p_eval.add_argument("--bench-n", type=int, default=100, help="Число запросов для замера p95")
    p_eval.add_argument("--data", default=None, help=f"индекс для прогона (default: {config.DATA_DIR})")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
