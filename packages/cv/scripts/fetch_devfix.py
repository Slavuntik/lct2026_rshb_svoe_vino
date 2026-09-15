"""Скачивание дев-фикстур: 50-60 эталонных фото бутылок из каталога vines.

НЕ датасет кейса (его нет до завтра) — только девелоперские фикстуры для
self-match / визуальной проверки нормализации / замеров латентности на этой
машине. Источник — `pipeline/catalog/wines/*.json` (`source.image_url`),
read-only данные vines.

Вежливость (по брифу агента G): 1 запрос/сек к хосту, свой User-Agent,
идемпотентность (уже скачанные файлы не перекачиваются повторно — полезно
при повторных запусках/сбоях сети).

Отдельный скрипт, а не часть пакета `cv`: использует только stdlib (urllib),
не тянет зависимости пакета, можно гонять до `uv pip install`. Использование:

    python3 scripts/fetch_devfix.py [--n 55] [--seed 20260915]

Пишет devfix/<slug>.<ext> + devfix/manifest.json (slug -> {file, url, sha256}).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "svoe-vino-lct-research (CV agent G dev-fixtures; contact: vyacheslav.fokin@gmail.com)"
RATE_LIMIT_S = 1.0  # 1 rps

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
WINES_DIR = REPO_ROOT / "pipeline" / "catalog" / "wines"
OUT_DIR = PACKAGE_ROOT / "devfix"
MANIFEST_PATH = OUT_DIR / "manifest.json"

# Обе пары near-duplicates из брифа — всегда включены, независимо от сэмплинга.
REQUIRED_SLUGS = ["aligote-barrel-2024", "aligote-barrel-2025"]


def _iter_catalog() -> list[tuple[str, str]]:
    """[(slug, image_url), ...] для всех карточек с непустым image_url."""
    out = []
    for f in sorted(WINES_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slug = d.get("slug")
        url = (d.get("source") or {}).get("image_url")
        if slug and url:
            out.append((slug, url))
    return out


def _ext_from_url(url: str) -> str:
    tail = url.rsplit("/", 1)[-1]
    if "." in tail:
        ext = tail.rsplit(".", 1)[-1].split("?")[0].lower()
        if 1 <= len(ext) <= 5 and ext.isalnum():
            return ext
    return "jpg"


def select_slugs(catalog: list[tuple[str, str]], n: int, seed: int) -> list[tuple[str, str]]:
    by_slug = {slug: url for slug, url in catalog}
    missing_required = [s for s in REQUIRED_SLUGS if s not in by_slug]
    if missing_required:
        print(f"ВНИМАНИЕ: обязательные near-dup slug'и не найдены в каталоге: {missing_required}", file=sys.stderr)

    chosen: dict[str, str] = {s: by_slug[s] for s in REQUIRED_SLUGS if s in by_slug}
    rest = [(s, u) for s, u in catalog if s not in chosen]
    rng = random.Random(seed)
    rng.shuffle(rest)
    for slug, url in rest:
        if len(chosen) >= n:
            break
        chosen[slug] = url
    return list(chosen.items())


def fetch_one(slug: str, url: str, out_dir: Path) -> dict | None:
    existing = list(out_dir.glob(f"{slug}.*"))
    existing = [p for p in existing if p.name != "manifest.json"]
    if existing:
        path = existing[0]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"  [skip] {slug} уже скачан -> {path.name}")
        return {"file": path.name, "url": url, "sha256": digest, "bytes": path.stat().st_size}

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  [fail] {slug} <- {url}: {e}", file=sys.stderr)
        return None

    ext = _ext_from_url(url)
    path = out_dir / f"{slug}.{ext}"
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f"  [ok]   {slug} <- {url} ({len(data)} bytes)")
    return {"file": path.name, "url": url, "sha256": digest, "bytes": len(data)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=55, help="сколько позиций всего (включая обе near-dup)")
    parser.add_argument("--seed", type=int, default=20260915, help="сид отбора случайной выборки")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = _iter_catalog()
    print(f"Каталог: {len(catalog)} позиций с image_url")
    chosen = select_slugs(catalog, args.n, args.seed)
    print(f"Отобрано: {len(chosen)} (включая near-dup пару)")

    manifest: dict[str, dict] = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}

    n_downloaded = 0
    for i, (slug, url) in enumerate(chosen):
        entry = fetch_one(slug, url, out_dir)
        if entry is not None:
            manifest[slug] = entry
            n_downloaded += 1
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if i < len(chosen) - 1:
            time.sleep(RATE_LIMIT_S)  # вежливость: 1 rps к api.vino-svoe.ru

    print(f"\nГотово: {n_downloaded}/{len(chosen)} успешно, манифест -> {MANIFEST_PATH}")
    return 0 if n_downloaded > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
