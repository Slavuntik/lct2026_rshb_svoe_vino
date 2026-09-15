#!/usr/bin/env python3
"""CLI пайплайна каталога vines.

    python run.py discover --entity wines
    python run.py fetch    --entity wines --concurrency 4 --delay 0.7
    python run.py parse    --entity wines
    python run.py normalize
    python run.py build-index
    python run.py report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx
from selectolax.parser import HTMLParser

from config import (
    BASE, BUILD, CATALOG, CONCURRENCY, DELAY_SEC, ENTITIES, HEADERS,
    PARSER_VERSION, RAW, RETRIES, SLUG_BLACKLIST, TIMEOUT_SEC,
)
from fetch import content_hash, fetch_many, meta_path, raw_path
from parse_article import parse_article
from parse_wine import parse_wine
from parse_winery import parse_winery


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

async def discover(entity: str, max_pages: int | None) -> list[str]:
    cfg = ENTITIES[entity]
    pattern = re.compile(cfg["link_re"])
    slugs: set[str] = set()
    page = 1
    pages_cap = max_pages or cfg["pages"] or 10_000

    async with httpx.AsyncClient(headers=HEADERS, http2=True) as client:
        while page <= pages_cap:
            url = cfg["list"].format(page=page)
            # Портал изредка роняет HTTP/2-соединение (GOAWAY). Одна такая ошибка
            # не повод обрывать обход: повторяем, а неподнявшуюся страницу пропускаем.
            resp = None
            for attempt in range(RETRIES):
                try:
                    resp = await client.get(url, timeout=TIMEOUT_SEC, follow_redirects=True)
                    resp.raise_for_status()
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"  стр. {page}: попытка {attempt + 1}/{RETRIES}: {exc}", file=sys.stderr)
                    resp = None
                    await asyncio.sleep(1.5 * (attempt + 1))

            if resp is None:
                print(f"  стр. {page}: пропущена после {RETRIES} попыток", file=sys.stderr)
                page += 1
                await asyncio.sleep(DELAY_SEC)
                continue

            found = set(pattern.findall(resp.text)) - SLUG_BLACKLIST
            new = found - slugs
            slugs |= found
            print(f"  стр. {page}: найдено {len(found)}, новых {len(new)}, всего {len(slugs)}")

            if not new and page > 1:
                break
            page += 1
            await asyncio.sleep(DELAY_SEC)

    dest = RAW / f"{entity}.slugs.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {"entity": entity, "discovered_at": time.strftime("%Y-%m-%d"), "slugs": sorted(slugs)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return sorted(slugs)


def load_slugs(entity: str) -> list[str]:
    path = RAW / f"{entity}.slugs.json"
    if not path.exists():
        sys.exit(f"нет {path} — сначала запусти: python run.py discover --entity {entity}")
    return json.loads(path.read_text(encoding="utf-8"))["slugs"]


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------

def do_fetch(entity: str, concurrency: int, delay: float, force: bool, limit: int | None) -> None:
    slugs = load_slugs(entity)
    if limit:
        slugs = slugs[:limit]
    cfg = ENTITIES[entity]
    jobs = [
        (
            cfg["detail"].format(slug=s),
            raw_path(RAW, entity, s),
            meta_path(RAW, entity, s),
        )
        for s in slugs
    ]
    print(f"выкачиваю {len(jobs)} стр. ({entity}), параллельно {concurrency}, пауза {delay}с")
    stats = asyncio.run(fetch_many(jobs, concurrency, delay, force))
    print("итог:", json.dumps(stats, ensure_ascii=False))


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------

PARSERS = {"wines": parse_wine, "wineries": parse_winery, "articles": parse_article}


def do_parse(entity: str) -> None:
    if entity not in PARSERS:
        sys.exit(f"парсер для '{entity}' ещё не написан — есть: {', '.join(PARSERS)}")
    parser = PARSERS[entity]
    src_dir = RAW / entity
    out_dir = CATALOG / entity
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = failed = 0
    for html_path in sorted(src_dir.glob("*.html")):
        slug = html_path.stem
        html = html_path.read_text(encoding="utf-8")
        try:
            doc = parser(html, slug)
        except Exception as exc:  # noqa: BLE001
            print(f"  {slug}: {exc}", file=sys.stderr)
            failed += 1
            continue

        meta_file = meta_path(RAW, entity, slug)
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
        doc["provenance"] = {
            "source_url": meta.get("source_url", ENTITIES[entity]["detail"].format(slug=slug)),
            "fetched_at": meta.get("fetched_at"),
            "content_hash": meta.get("content_hash", content_hash(html)),
            "parser_version": PARSER_VERSION,
        }
        (out_dir / f"{slug}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok += 1
    print(f"разобрано {ok}, ошибок {failed}")


# --------------------------------------------------------------------------
# build-index
# --------------------------------------------------------------------------

def do_build_index() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "index.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for path in sorted((CATALOG / "wines").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            s, d = doc["source"], doc.get("derived", {})
            # плоский документ для гибридного поиска: BM25 по text, фильтры по атрибутам
            text = " · ".join(
                filter(
                    None,
                    [
                        s.get("name"), s.get("winery_name"), s.get("region_name"),
                        ", ".join(s.get("grapes", [])),
                        s.get("color"), s.get("sugar_category"), s.get("color_in_glass"),
                        ", ".join(s.get("food_pairings", [])),
                        s.get("description"),
                    ],
                )
            )
            fh.write(
                json.dumps(
                    {
                        "id": doc["slug"],
                        "text": text,
                        "url": doc["provenance"]["source_url"],
                        "filters": {
                            "color": s.get("color"),
                            "sugar": s.get("sugar_category"),
                            "region": s.get("region"),
                            "winery": s.get("winery"),
                            "grapes": d.get("grape_slugs", []),
                            "food": s.get("food_pairings", []),
                            "rating": s.get("public_rating"),
                            "abv": s.get("abv_percent"),
                        },
                        "sensory": d.get("sensory", {}),
                        "styles": d.get("reference_style_matches", []),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    print(f"index.jsonl: {n} записей -> {out}")

    build_index_wineries()
    build_index_articles()


def build_index_wineries() -> None:
    """Отдельный индекс виноделен: схема вина сюда не годится."""
    out = BUILD / "wineries.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for path in sorted((CATALOG / "wineries").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            s = doc["source"]
            text = " · ".join(
                filter(
                    None,
                    [
                        s.get("name"), s.get("region_name"), s.get("locality"),
                        s.get("climate"), s.get("description"), s.get("about"),
                    ],
                )
            )
            fh.write(
                json.dumps(
                    {
                        "id": doc["slug"],
                        "type": "winery",
                        "text": text,
                        "url": doc["provenance"]["source_url"],
                        "filters": {
                            "region": s.get("region_name"),
                            "vineyard_area_ha": s.get("vineyard_area_ha"),
                            "founded_year": s.get("founded_year"),
                            "wines": len(s.get("wine_slugs", [])),
                        },
                        "wine_slugs": s.get("wine_slugs", []),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    print(f"wineries.jsonl: {n} записей -> {out}")


def build_index_articles() -> None:
    """Индекс статей. Тело режем на чанки по заголовкам разделов —
    целая статья на 4000 знаков для поиска слишком крупная единица."""
    out = BUILD / "articles.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for path in sorted((CATALOG / "articles").glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            s = doc["source"]
            if not s.get("title"):
                continue
            body = s.get("body") or ""
            for i, chunk in enumerate(chunk_article(body, s.get("headings", []))):
                fh.write(
                    json.dumps(
                        {
                            "id": f"{doc['slug']}#{i}",
                            "type": "article",
                            "article_id": doc["slug"],
                            "title": s["title"],
                            "heading": chunk["heading"],
                            "text": chunk["text"],
                            "url": doc["provenance"]["source_url"],
                            "filters": {
                                "rubric": s.get("rubric"),
                                "author": s.get("author"),
                                "published_date": s.get("published_date"),
                                "year": s.get("published_year"),
                            },
                            "wine_slugs": s.get("wine_slugs", []),
                            "winery_slugs": s.get("winery_slugs", []),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                n += 1
    print(f"articles.jsonl: {n} чанков -> {out}")


# Потолок чанка. Лонгриды без заголовков иначе дают куски по 25 тыс. знаков,
# бесполезные и для BM25, и для эмбеддингов.
MAX_CHUNK = 2000


def _split_long(chunk: dict[str, str]) -> list[dict[str, str]]:
    """Дробит слишком длинный кусок по границам абзацев."""
    text = chunk["text"]
    if len(text) <= MAX_CHUNK:
        return [chunk]

    parts: list[dict[str, str]] = []
    buf: list[str] = []
    size = 0
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if size and size + len(para) > MAX_CHUNK:
            parts.append({"heading": chunk["heading"], "text": "\n\n".join(buf)})
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        parts.append({"heading": chunk["heading"], "text": "\n\n".join(buf)})
    return parts or [chunk]


def chunk_article(body: str, headings: list[str]) -> list[dict[str, str]]:
    """Режет текст статьи по заголовкам разделов, затем дробит переростки.
    Если заголовков нет — отдаёт статью одним куском (и он тоже дробится)."""
    if not body:
        return []
    if not headings:
        return _split_long({"heading": None, "text": body})

    positions: list[tuple[int, str]] = []
    for h in headings:
        idx = body.find(h)
        if idx != -1:
            positions.append((idx, h))
    positions.sort()
    if not positions:
        return _split_long({"heading": None, "text": body})

    chunks: list[dict[str, str]] = []
    if positions[0][0] > 0:
        lead = body[: positions[0][0]].strip()
        if lead:
            chunks.append({"heading": None, "text": lead})
    for i, (idx, h) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(body)
        text = body[idx:end].strip()
        if text:
            chunks.append({"heading": h, "text": text})

    out: list[dict[str, str]] = []
    for c in chunks:
        out.extend(_split_long(c))
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

FIELDS = {
    "wines": [
        "name", "winery", "region_name", "grapes", "color", "sugar_category",
        "color_in_glass", "vintage", "abv_percent", "serving_temp_c",
        "food_pairings", "description", "public_rating", "image_url",
    ],
    "wineries": [
        "name", "region_name", "locality", "climate", "vineyard_area_ha",
        "founded_year", "description", "about", "image_url", "wine_slugs",
    ],
    "articles": [
        "title", "lead", "body", "headings", "rubric", "author",
        "photo_credit", "published_date", "image_url",
    ],
}


def _coverage(entity: str) -> list[dict] | None:
    docs = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (CATALOG / entity).glob("*.json")
    ]
    if not docs:
        return None
    total = len(docs)
    print(f"\n=== {entity}: {total} ===\n")
    print(f"{'поле':<20} {'заполнено':>10} {'%':>7}")
    print("-" * 40)
    for f in FIELDS[entity]:
        filled = sum(1 for d in docs if d["source"].get(f) not in (None, "", [], {}))
        print(f"{f:<20} {filled:>10} {filled / total * 100:>6.1f}%")
    return docs


def do_report(entity: str | None = None) -> None:
    entities = [entity] if entity else list(FIELDS)
    seen = False

    for ent in entities:
        docs = _coverage(ent)
        if docs is None:
            continue
        seen = True

        if ent == "wines":
            conf = [d.get("derived", {}).get("sensory", {}).get("confidence", 0) for d in docs]
            print(f"\nсредняя уверенность сенсорного вектора: {sum(conf) / len(conf):.2f}")
            _style_stats(docs)

    if not seen:
        sys.exit("каталог пуст")

    _referential_check()


def _style_stats(docs: list[dict]) -> None:
    """Матчинг эталонных стилей — в обе стороны.

    Одной доли «вин со стилем» мало: она была 100% и при заведомо сломанном
    матчере. Смотрим ещё, сколько разных стилей реально задействовано и у
    скольких стилей вообще нашёлся российский аналог.
    """
    from collections import Counter

    from normalize import STYLE_MATCH_THRESHOLD, load_ref

    used: Counter = Counter()
    matched = 0
    for d in docs:
        m = d.get("derived", {}).get("reference_style_matches") or []
        if m:
            matched += 1
        used.update(m)

    total_styles = len({s["slug"] for s in load_ref("reference_styles.yaml")["styles"]})
    print(f"\n--- эталонные стили (порог {STYLE_MATCH_THRESHOLD}) ---")
    print(f"вин с матчем: {matched} из {len(docs)} ({matched / len(docs) * 100:.1f}%)")
    print(f"задействовано стилей: {len(used)} из {total_styles}")
    print(f"стилей без российского аналога: {total_styles - len(used)}")
    if used:
        top = ", ".join(f"{s} ({n})" for s, n in used.most_common(5))
        print(f"чаще всего: {top}")


def _referential_check() -> None:
    """Связность каталога: на что ссылаются вина и есть ли это в каталоге."""
    wines = [json.loads(p.read_text(encoding="utf-8")) for p in (CATALOG / "wines").glob("*.json")]
    wineries = {p.stem for p in (CATALOG / "wineries").glob("*.json")}
    if not wines or not wineries:
        return
    linked = [w for w in wines if w["source"].get("winery")]
    referenced = {w["source"]["winery"] for w in linked}
    missing = sorted(referenced - wineries)
    print("\n--- связность ---")
    print(f"вин со ссылкой на винодельню: {len(linked)} из {len(wines)}")
    print(f"виноделен в каталоге: {len(wineries)}, из них упомянуто винами: {len(referenced & wineries)}")
    print(f"битых ссылок вино -> винодельня: {len(missing)}" + (f" {missing[:5]}" if missing else ""))


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="пайплайн каталога vines")
    ap.add_argument("command", choices=["discover", "fetch", "parse", "normalize", "build-index", "report"])
    ap.add_argument("--entity", default="wines", choices=list(ENTITIES))
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    ap.add_argument("--pages", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.command == "discover":
        slugs = asyncio.run(discover(args.entity, args.pages))
        print(f"найдено {len(slugs)} slug'ов -> raw/{args.entity}.slugs.json")
    elif args.command == "fetch":
        do_fetch(args.entity, args.concurrency, args.delay, args.force, args.limit)
    elif args.command == "parse":
        do_parse(args.entity)
    elif args.command == "normalize":
        from normalize import normalize_all
        print(json.dumps(normalize_all(), ensure_ascii=False, indent=2))
    elif args.command == "build-index":
        do_build_index()
    elif args.command == "report":
        do_report()


if __name__ == "__main__":
    main()
