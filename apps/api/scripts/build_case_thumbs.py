#!/usr/bin/env python3
"""apps/api/scripts/build_case_thumbs.py — превью эталонов кейса для
фолбэк-карточки (agents/B8-candidates-card.md, contracts/image-scan.md
v0.4.11 п.4). Источник — `CASE_DATA_DIR/slug_refs.json` (генератор
qa/case_census.py; `app/cv/case_catalog.py` уже читает этот файл для другой
задачи — метаданные OCR-верификатора) + оригиналы файлов в
`CASE_DATA_DIR/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads/`.

Только USABLE-слаги (контракт: "выбранный эталон КАЖДОГО usable слага") — у
остальных `chosen` не значит "нормальное фото бутылки", census уже отсеял их
в отдельные классы (label_closeup вне usable, logo_graphic и т.п.).

Запуск (из apps/api, venv активен — Pillow нужен, см. pyproject.toml):
    python scripts/build_case_thumbs.py
Выход — <CASE_DATA_DIR>/thumbs/<slug>.webp, длинная сторона <= 480 px (без
апскейла у изображений, которые уже меньше), quality=80 — раздаётся `GET
/v1/case-thumbs/{slug}.webp` (app/routers/case_thumbs.py) как есть, без
ресайза на лету.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image, ImageOps

_DEFAULT_CASE_DATA_DIR = "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"
_UPLOADS_REL = Path("prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads")
_LONG_SIDE = 480
_QUALITY = 80


def _case_data_dir() -> Path:
    return Path(os.environ.get("CASE_DATA_DIR", _DEFAULT_CASE_DATA_DIR))


def _load_usable_refs(slug_refs_path: Path) -> dict[str, str]:
    """slug -> имя файла эталона (`chosen`), только usable=True (контракт:
    "выбранный эталон КАЖДОГО USABLE слага")."""
    payload = json.loads(slug_refs_path.read_text(encoding="utf-8"))
    mapping = payload.get("mapping") or {}
    result: dict[str, str] = {}
    for slug, entry in mapping.items():
        if not entry.get("usable"):
            continue
        chosen = entry.get("chosen")
        if chosen:
            result[slug] = chosen
    return result


def _resize_long_side(im: Image.Image, long_side: int) -> Image.Image:
    width, height = im.size
    longest = max(width, height)
    if longest <= long_side:
        return im  # не апскейлим — контракт просит "длинная сторона 480 px" как потолок
    ratio = long_side / longest
    new_size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return im.resize(new_size, Image.LANCZOS)


def build_thumbs(
    case_dir: Path, *, long_side: int = _LONG_SIDE, quality: int = _QUALITY
) -> tuple[int, int, int]:
    """Возвращает (сгенерировано, пропущено-нет-исходника, всего-usable)."""
    slug_refs_path = case_dir / "slug_refs.json"
    uploads_dir = case_dir / _UPLOADS_REL
    out_dir = case_dir / "thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)

    refs = _load_usable_refs(slug_refs_path)
    generated = 0
    missing = 0
    for slug, chosen in refs.items():
        src_path = uploads_dir / chosen
        if not src_path.is_file():
            missing += 1
            continue
        with Image.open(src_path) as im:
            im = ImageOps.exif_transpose(im)  # эталоны каталога — на всякий случай, как и archive.py
            im = im.convert("RGB")  # webp с альфой/CMYK -> RGB, тот же приём, что app/cv/archive.py
            im = _resize_long_side(im, long_side)
            im.save(out_dir / f"{slug}.webp", "WEBP", quality=quality)
        generated += 1
    return generated, missing, len(refs)


def main() -> None:
    case_dir = _case_data_dir()
    slug_refs_path = case_dir / "slug_refs.json"
    if not slug_refs_path.exists():
        raise SystemExit(f"slug_refs.json не найден: {slug_refs_path}")

    generated, missing, total = build_thumbs(case_dir)
    out_dir = case_dir / "thumbs"
    total_bytes = sum(p.stat().st_size for p in out_dir.glob("*.webp"))
    print(
        f"{generated}/{total} превью сгенерировано в {out_dir} "
        f"({missing} без исходного файла), {total_bytes / 1024 / 1024:.1f} МиБ суммарно"
    )


if __name__ == "__main__":
    main()
