#!/usr/bin/env python3
"""apps/api/scripts/scan_to_chat_eval.py — метрика «скан -> сомелье»
(reports/backend-rag-rebuild.md, 22.09, задача тимлида п.3).

Для каждого вина каталога сканера (case-data/case_catalog.json, 2103 слага
на 22.09) строит РОВНО тот вопрос, что шлёт кнопка "Спросить сомелье об этом
вине": apps/web/src/app/scan/ScanScreen.tsx:170 и apps/web/src/app/wine/
WineCardScreen.tsx:57 оба зовут
    t("chat.prefillAskAboutWine", {name: wine.source.name, winery: wine.source.winery_name})
apps/web/src/i18n/ru.ts:156: prefillAskAboutWine = "Расскажи про {name} от {winery}".
apps/web/src/i18n/translate.ts::interpolate — чистая подстановка `{key}` ->
String(vars[key]), без каких-либо трансформаций (не обрезает, не меняет
регистр); `String(null)` в JS == "null" (НЕ Python "None") — учтено в
_js_string() ниже, встречается редко (source.winery_name пуст у единиц
карточек vines).

Дальше — ТОТ ЖЕ путь, что POST /v1/chat (app/routers/chat.py ->
app/chat/service.py::stream_chat_events, top_k=8 по умолчанию):
    filters = extract_filters(message)                       # app/chat/filters.py
    candidates = retriever.search(message, filters=filters, top_k=8)  # rag.base.Retriever, collections=None (автомаршрутизация)
и проверяет, попал ли slug САМОГО этого вина в id кандидатов (то, что уходит
в промпт LLM). Реранкер — настоящий (jina-reranker-v2-base-multilingual,
конфиг по умолчанию packages/rag/rag/config.py) — НЕ NoOpReranker, иначе
порядок/refusal не совпадают с боевым путём.

name/winery_name резолвятся ТОЙ ЖЕ логикой, что строит карточку вина
(app/rag/cards.py::build_wine_card): сначала RAG (retriever.get_by_id),
иначе фолбэк app/rag/case_catalog.py::lookup() — воспроизведено здесь
напрямую (без похода в build_wine_card целиком, там ещё считается
`similar`, который этому скрипту не нужен и только тратит время на 2103x2
вызовах).

Запуск (из apps/api, venv активен, packages/rag установлен editable —
`uv sync --extra integration`, см. reports/backend-rag-rebuild.md):
    python scripts/scan_to_chat_eval.py --data-dir <индекс> --label old --out <файл.jsonl>
    python scripts/scan_to_chat_eval.py --data-dir <индекс> --label new --out <файл.jsonl>

Пишет <файл.jsonl> построчно (по мере прогона — resume-friendly: повторный
запуск с тем же --out пропускает уже посчитанные слаги) + по окончании
печатает сводку в stdout (summarize_only читает готовый .jsonl без пересчёта).
Долго: реранкер кросс-энкодер ~0.6-0.9с/запрос (packages/rag/eval/report.json,
with_reranker.p50_ms=687) x 2103 слага — ориентир 25-40 минут НА ИНДЕКС на
CPU этой машины, поэтому запускать в фоне.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_APPS_API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APPS_API_ROOT))

from app.chat.filters import extract_filters  # noqa: E402
from app.rag import case_catalog as rag_case_catalog  # noqa: E402
from rag.base import Retriever  # noqa: E402
from rag.case_data import load_case_catalog  # noqa: E402

_QUERY_TEMPLATE = "Расскажи про {name} от {winery}"  # apps/web/src/i18n/ru.ts:156, дословно
_TOP_K = 8  # apps/api/app/chat/service.py::stream_chat_events(top_k=8), дефолт /v1/chat


def _js_string(value) -> str:
    """String(value) в JS: null -> "null" (см. докстринг модуля)."""
    return "null" if value is None else str(value)


def resolve_name_winery(retriever: Retriever, slug: str) -> tuple[str | None, str | None] | None:
    """Копия резолва app/rag/cards.py::build_wine_card (только name/winery_name)."""
    candidate = retriever.get_by_id(slug)
    if candidate is not None and candidate.kind == "wine":
        source = candidate.meta.get("source") or {}
        return source.get("name"), source.get("winery_name")
    case_wine = rag_case_catalog.lookup(slug)
    if case_wine is None:
        return None
    return case_wine.name, case_wine.winery_name


def classify_miss(retriever: Retriever, slug: str, candidates, target_winery_name: str | None) -> str:
    """not_in_index | empty_result | family_or_vintage_twin | other_ranked_out.

    family_or_vintage_twin — среди вернувшихся кандидатов kind=wine есть хотя
    бы один с ТЕМ ЖЕ winery_name (регистронезависимо), что у искомого вина —
    типичная сигнатура и "близнецов в семье" (несколько похожих SKU одной
    винодельни), и вариантов урожая (case_catalog.json не несёт vintage
    отдельным полем, см. reports/backend-rag-rebuild.md — различить их между
    собой без vintage нельзя, обе причины сведены в одну категорию честно,
    не выдумывая точность, которой нет в данных)."""
    if slug not in retriever.payload_by_id.get("wines", {}):
        return "not_in_index"
    if not candidates:
        return "empty_result"
    if target_winery_name:
        target_w = target_winery_name.strip().lower()
        for c in candidates:
            if c.kind != "wine":
                continue
            cand_winery = (c.meta.get("source") or {}).get("winery_name") or ""
            if cand_winery.strip().lower() == target_w:
                return "family_or_vintage_twin"
    return "other_ranked_out"


def _iter_target_slugs(case_data_dir: Path) -> list[str]:
    mapping = load_case_catalog(case_data_dir)
    return sorted(mapping)


def run(
    data_dir: Path, case_data_dir: Path, label: str, out_path: Path, limit: int | None,
    use_wine_id: bool = False,
) -> None:
    """`use_wine_id=True` (v0.3.5, openapi 0.3.5, app/chat/service.py::
    stream_chat_events(wine_id=)): симулирует фронт, передающий wine_id =
    слаг ИМЕННО ЭТОГО отсканированного/открытого вина — ровно тот путь,
    которым в реальности будет пользоваться кнопка "Спросить сомелье".
    Пин детерминирован кодом (candidates = [pinned] + [...], БЕЗ повторного
    среза по top_k дальше в stream_chat_events) — если `retriever.get_by_id(
    slug)` резолвится в kind="wine", попадание в top-8 гарантировано
    построением, независимо от того, что вернул бы search(). Поэтому здесь
    НЕ зовём search()/реранкер в этой ветке вовсе (на 2103 слагах это часы
    экономии CPU без потери точности измерения) — только когда get_by_id()
    НЕ резолвит (ожидаем ~0 случаев на полном индексе), честно падаем на
    обычный путь search(), чтобы не соврать про причину промаха."""
    already_done: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    already_done.add(json.loads(line)["slug"])
        print(f"[{label}] resume: {len(already_done)} слагов уже посчитаны в {out_path}", file=sys.stderr)

    slugs = _iter_target_slugs(case_data_dir)
    if limit:
        slugs = slugs[:limit]
    todo = [s for s in slugs if s not in already_done]

    print(f"[{label}] грузим Retriever(data_dir={data_dir}) ...", file=sys.stderr)
    t0 = time.perf_counter()
    retriever = Retriever(data_dir=data_dir)  # настоящий реранкер, см. докстринг модуля
    print(f"[{label}] Retriever готов за {time.perf_counter() - t0:.1f}s, index_version={retriever.index_version}", file=sys.stderr)
    print(f"[{label}] всего слагов: {len(slugs)}, уже готово: {len(already_done)}, осталось: {len(todo)}", file=sys.stderr)

    with open(out_path, "a", encoding="utf-8") as out_f:
        t_start = time.perf_counter()
        for i, slug in enumerate(todo, start=1):
            resolved = resolve_name_winery(retriever, slug)
            if resolved is None:
                row = {"slug": slug, "hit": False, "reason": "no_card_at_all", "query": None, "winery_name": None}
            else:
                name, winery = resolved
                query = _QUERY_TEMPLATE.format(name=_js_string(name), winery=_js_string(winery))

                pinned = retriever.get_by_id(slug) if use_wine_id else None
                if pinned is not None and pinned.kind == "wine":
                    # Пин гарантирует попадание — см. докстринг run(). search()
                    # намеренно не зовём (совпадает с реальным исходом, экономит CPU).
                    row = {
                        "slug": slug, "query": query, "hit": True, "reason": None,
                        "winery_name": winery, "candidate_ids": [slug], "wine_id_pinned": True,
                    }
                else:
                    filters = extract_filters(query)
                    candidates = retriever.search(query, filters=filters, top_k=_TOP_K)
                    candidate_ids = [c.id for c in candidates]
                    hit = slug in candidate_ids
                    reason = None if hit else classify_miss(retriever, slug, candidates, winery)
                    row = {
                        "slug": slug,
                        "query": query,
                        "hit": hit,
                        "reason": reason,
                        "winery_name": winery,
                        "candidate_ids": candidate_ids,
                        # get_by_id(slug) не резолвился в kind="wine" — пин не сработал,
                        # это фолбэк на обычный search() (ожидаем ~0 таких на полном индексе).
                        **({"wine_id_pinned": False} if use_wine_id else {}),
                    }
            out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
            out_f.flush()
            if i % 50 == 0 or i == len(todo):
                elapsed = time.perf_counter() - t_start
                rate = i / elapsed if elapsed > 0 else 0.0
                eta_min = (len(todo) - i) / rate / 60 if rate > 0 else float("nan")
                print(
                    f"[{label}] {i}/{len(todo)} готово, {elapsed:.0f}s прошло, "
                    f"~{rate:.2f} слага/с, ETA {eta_min:.1f} мин",
                    file=sys.stderr,
                )

    print(f"[{label}] готово: {out_path}", file=sys.stderr)
    summarize(out_path, label)


def summarize(out_path: Path, label: str) -> dict:
    rows = []
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    total = len(rows)
    hits = sum(1 for r in rows if r["hit"])
    by_reason: dict[str, int] = {}
    by_winery_misses: dict[str, int] = {}
    for r in rows:
        if r["hit"]:
            continue
        reason = r.get("reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + 1
        winery = r.get("winery_name") or "(без винодельни)"
        by_winery_misses[winery] = by_winery_misses.get(winery, 0) + 1

    summary = {
        "label": label,
        "out_path": str(out_path),
        "total": total,
        "hits": hits,
        "hit_rate": round(hits / total, 4) if total else None,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "by_winery_misses_top20": dict(sorted(by_winery_misses.items(), key=lambda kv: -kv[1])[:20]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="индекс RAG (packages/rag/data-совместимый каталог)")
    parser.add_argument("--case-data-dir", default=None, help="default: $CASE_DATA_DIR или /Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
    parser.add_argument("--label", required=True, help="метка индекса в выводе (например 'old'/'new')")
    parser.add_argument("--out", required=True, help="путь к .jsonl с построчным результатом (resume-friendly)")
    parser.add_argument("--limit", type=int, default=None, help="ограничить числом слагов (смок-тест)")
    parser.add_argument("--summarize-only", action="store_true", help="не считать заново — только сводка по готовому --out")
    parser.add_argument(
        "--wine-id", action="store_true",
        help="симулировать ChatRequest.wine_id=<свой слаг> (v0.3.5) — см. докстринг run()",
    )
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.summarize_only:
        summarize(out_path, args.label)
        return 0

    import os

    case_data_dir = Path(args.case_data_dir) if args.case_data_dir else Path(
        os.environ.get("CASE_DATA_DIR", "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data")
    )
    run(Path(args.data_dir), case_data_dir, args.label, out_path, args.limit, use_wine_id=args.wine_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
