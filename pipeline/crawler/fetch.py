"""Загрузка страниц с дисковым кэшем.

Идемпотентность: если файл уже в raw/ и не задан --force, сеть не трогаем.
Это позволяет перепарсивать сколько угодно раз без повторной выкачки.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import httpx

from config import DELAY_SEC, HEADERS, RETRIES, TIMEOUT_SEC


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_path(base: Path, entity: str, slug: str) -> Path:
    return base / entity / f"{slug}.html"


def meta_path(base: Path, entity: str, slug: str) -> Path:
    return base / entity / f"{slug}.meta.json"


async def fetch_one(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    meta_dest: Path,
    force: bool = False,
) -> tuple[str, bool]:
    """Возвращает (статус, был_ли_сетевой_запрос)."""
    if dest.exists() and not force:
        return "cached", False

    last_err = None
    for attempt in range(RETRIES):
        try:
            resp = await client.get(url, timeout=TIMEOUT_SEC, follow_redirects=True)
            if resp.status_code == 404:
                return "404", True
            resp.raise_for_status()
            text = resp.text
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            meta_dest.write_text(
                json.dumps(
                    {
                        "source_url": str(resp.url),
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "status_code": resp.status_code,
                        "content_hash": content_hash(text),
                        "etag": resp.headers.get("etag"),
                        "bytes": len(text),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            return "ok", True
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    return f"error: {last_err}", True


async def fetch_many(
    urls: list[tuple[str, Path, Path]],
    concurrency: int,
    delay: float = DELAY_SEC,
    force: bool = False,
) -> dict[str, int]:
    sem = asyncio.Semaphore(concurrency)
    stats: dict[str, int] = {}

    async with httpx.AsyncClient(headers=HEADERS, http2=True) as client:

        async def worker(url: str, dest: Path, meta_dest: Path) -> None:
            async with sem:
                status, hit_network = await fetch_one(client, url, dest, meta_dest, force)
                key = status.split(":")[0]
                stats[key] = stats.get(key, 0) + 1
                if hit_network:
                    await asyncio.sleep(delay)

        await asyncio.gather(*(worker(u, d, m) for u, d, m in urls))

    return stats
