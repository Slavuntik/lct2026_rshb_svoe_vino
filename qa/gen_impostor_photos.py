#!/usr/bin/env python3
"""qa/gen_impostor_photos.py — impostor-холдаут для калибровки ветки not_in_catalog
(агент F2, поручение оркестратора после ревью 04: "порог 0.55 при реальных скорах 0.8-1.0
почти не срабатывает, а ветка «нет в каталоге» ТЗ оценивает особенно высоко").

Два НЕЗАВИСИМЫХ источника "заведомо не в каталоге" фото — умышленно оба, не один, потому
что они моделируют разные угрозы:

  1. **held-out реальные вина** (`HOLDOUT_SLUGS`) — N настоящих вин devfix, физически
     ИСКЛЮЧЁННЫХ из калибровочного индекса (см. `build_calibration_refs`). Их полевые фото
     рендерятся ТЕМ ЖЕ аугментатором G, что и позитивный набор — реалистичный тест "вино
     похожего типа/стиля, которого просто нет в ЭТОМ индексе" (ревью 04, чек-лист п.3: "вынуть
     N позиций из индекса"). Выбраны из хвоста sorted(devfix) — той части (индексы
     `N_BASE_SLUGS:N_BASE_SLUGS+HOLDOUT_SIZE`), что НИКОГДА не участвовала в позитивном
     наборе `qa/gen_synthetic_scan_photos.py` (см. импорт `N_BASE_SLUGS` оттуда — одна
     константа, не рассинхронизировать две копии) — ноль пересечения по построению, не по
     совпадению.
  2. **синтетические "выдуманные этикетки"** (`generate_fake_label_texture`) — процедурно
     нарисованные PIL-канвасы (случайный фон, псевдослова из слоговых обрубков, декоративная
     рамка/эмблема) — легальный, без каких-либо реальных брендов источник "визуально похоже
     на этикетку, но никогда не было вином" (задание оркестратора: "хоть свои синтетические
     «выдуманные этикетки» аугментатором с фейковым текстом"). Тот же `render_synthetic_views`
     превращает их в "полевые" фото — методологически на равных с остальным набором.

Оба источника прогоняются ЧЕРЕЗ ОДИН И ТОТ ЖЕ аугментатор при ОДНОМ семействе искажений —
единственная переменная, которая меняется — содержимое "этикетки", не пайплайн рендера.

Запускать ТОЛЬКО через venv пакета cv (numpy/torch/opencv/pillow):
    packages/cv/.venv/bin/python qa/gen_impostor_photos.py

Пишет (всё — в qa/synthetic/, уже покрыто qa/.gitignore записью `synthetic/`):
    qa/synthetic/impostor-calibration/refs.csv             — 46 эталонов БЕЗ holdout (для
                                                              cv build-index --refs <csv>)
    qa/synthetic/impostor-calibration/photos/*.jpg         — импостор-фото (оба источника)
    qa/synthetic/impostor-calibration/labels.csv           — filename,slug,source,seed
    qa/synthetic/impostor-calibration/GENERATION.json      — параметры прогона

Чистая логика (discover/pick/build_calibration_refs) не тянет тяжёлые импорты — тестируется
под обычным qa/.venv (qa/test_synthetic_baseline.py).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_DEVFIX_DIR = _REPO_ROOT / "packages" / "cv" / "devfix"
_OUT_DIR = _QA_DIR / "synthetic" / "impostor-calibration"
_PHOTOS_DIR = _OUT_DIR / "photos"
_REFS_CSV = _OUT_DIR / "refs.csv"
_LABELS_CSV = _OUT_DIR / "labels.csv"
_META_JSON = _OUT_DIR / "GENERATION.json"

sys.path.insert(0, str(_QA_DIR))
from gen_synthetic_scan_photos import (  # noqa: E402 — путь добавлен строкой выше
    N_BASE_SLUGS,
    _stable_seed_for,
    discover_devfix_slugs,
)

HOLDOUT_SIZE = 10
VIEWS_PER_HOLDOUT_SLUG = 3  # 10 * 3 = 30 "held-out real" impostor фото

N_FAKE_LABELS = 15  # 15 синтетических "выдуманных этикеток", 1 ракурс каждая
FAKE_LABEL_SEED_BASE = 991337  # свой домен — не пересекается ни с одним другим seed в проекте


def pick_holdout_slugs(all_slugs: list[str], n_base: int = N_BASE_SLUGS, n_holdout: int = HOLDOUT_SIZE) -> list[str]:
    """N настоящих вин, НИКОГДА не участвовавших в позитивном наборе (`all_slugs[:n_base]` —
    ровно то, что `gen_synthetic_scan_photos.build_generation_plan` использует как базу) —
    нулевое пересечение по построению. Чистая функция — тестируется без диска/аугментатора."""
    return all_slugs[n_base : n_base + n_holdout]


def build_calibration_refs(all_slugs: list[str], holdout_slugs: list[str]) -> list[str]:
    """Эталоны калибровочного индекса — все devfix МИНУС held-out. Порядок сохранён
    (детерминированный, как и `all_slugs`)."""
    holdout_set = set(holdout_slugs)
    return [s for s in all_slugs if s not in holdout_set]


def _find_ref_file(devfix_dir: Path, slug: str) -> Path | None:
    direct = devfix_dir / f"{slug}.webp"
    if direct.is_file():
        return direct
    candidates = [c for c in devfix_dir.glob(f"{slug}.*")]
    return candidates[0] if candidates else None


# --- синтетические "выдуманные этикетки" -------------------------------------------------

# Слоговые обрубки для псевдослов — специально НЕ настоящие слова ни одного языка, только
# фонетически правдоподобные фрагменты (тот же принцип, что процедурные фоны аугментатора
# G: не тащить в репозиторий чужой контент, здесь — не тащить чей-либо настоящий бренд).
_FAKE_SYLLABLES = [
    "cha", "teau", "dom", "aine", "vil", "la", "mon", "fort", "bel", "air",
    "ro", "san", "del", "mar", "ost", "grand", "clos", "haut", "sol", "ven",
]


def _fake_word(rng, n_syllables: int) -> str:
    return "".join(rng.choice(_FAKE_SYLLABLES) for _ in range(n_syllables)).capitalize()


def generate_fake_label_texture(seed: int, size: tuple[int, int] = (520, 720)):
    """Процедурная "этикетка": случайный фон, декоративная рамка + эмблема-круг,
    2-3 строки псевдослов. Детерминировано по seed (`random.Random(seed)` — не
    numpy.random, здесь не нужна согласованность с аугментатором, только собственный
    детерминизм этой функции). Возвращает RGB uint8 ndarray (H, W, 3), совместимый со
    входом `cv.augment.render_synthetic_views`."""
    import math
    import random as _random

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    rng = _random.Random(seed)
    w, h = size
    palette = [
        (245, 240, 225), (250, 250, 250), (232, 224, 200),
        (30, 20, 20), (60, 20, 25), (20, 35, 25), (15, 15, 15),
    ]
    bg = palette[rng.randrange(len(palette))]
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    fg = (20, 20, 20) if sum(bg) > 400 else (235, 225, 200)

    margin = int(min(w, h) * 0.07)
    draw.rectangle([margin, margin, w - margin, h - margin], outline=fg, width=rng.randint(2, 5))
    inner = margin + int(min(w, h) * 0.02)
    draw.rectangle([inner, inner, w - inner, h - inner], outline=fg, width=1)

    cx, cy = w // 2, int(h * 0.3)
    r = int(min(w, h) * 0.13)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fg, width=3)
    n_rays = rng.randint(4, 7)
    for i in range(n_rays):
        angle = 2 * math.pi * i / n_rays + rng.uniform(-0.2, 0.2)
        x2, y2 = cx + r * 0.65 * math.cos(angle), cy + r * 0.65 * math.sin(angle)
        draw.line([cx, cy, x2, y2], fill=fg, width=2)

    try:
        font = ImageFont.load_default(size=34)
    except TypeError:  # Pillow < 10.1 без параметра size у load_default
        font = ImageFont.load_default()

    lines = [
        " ".join(_fake_word(rng, rng.randint(1, 2)) for _ in range(rng.randint(1, 2))).upper(),
        _fake_word(rng, 2).upper(),
        str(rng.randint(2015, 2023)),
    ]
    y = int(h * 0.52)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((w - tw) / 2, y), line, fill=fg, font=font)
        y += int(h * 0.09)

    return np.asarray(img, dtype=np.uint8)


def main() -> int:
    from cv import imageio  # лениво: тяжёлые зависимости нужны только для реального прогона
    from cv.augment import render_synthetic_views

    if not _DEVFIX_DIR.is_dir():
        print(f"devfix не найден: {_DEVFIX_DIR}", file=sys.stderr)
        return 2

    all_slugs = discover_devfix_slugs(_DEVFIX_DIR)
    holdout_slugs = pick_holdout_slugs(all_slugs)
    calibration_refs = build_calibration_refs(all_slugs, holdout_slugs)
    if len(holdout_slugs) < HOLDOUT_SIZE:
        print(f"недостаточно devfix-слагов для holdout: {len(holdout_slugs)} < {HOLDOUT_SIZE}", file=sys.stderr)
        return 2

    _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    # --- refs.csv калибровочного индекса (46 эталонов, БЕЗ held-out) --------------------
    with _REFS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["slug", "image_path"])
        writer.writeheader()
        for slug in calibration_refs:
            ref = _find_ref_file(_DEVFIX_DIR, slug)
            if ref is None:
                print(f"эталон для {slug!r} не найден — пропущен из калибровочного индекса", file=sys.stderr)
                continue
            writer.writerow({"slug": slug, "image_path": str(ref)})

    rows: list[dict] = []

    # --- источник 1: held-out реальные вина ---------------------------------------------
    for slug in holdout_slugs:
        ref = _find_ref_file(_DEVFIX_DIR, slug)
        if ref is None:
            print(f"held-out эталон для {slug!r} не найден — пропущен", file=sys.stderr)
            continue
        img = imageio.load_image_file(str(ref))
        for view_i in range(1, VIEWS_PER_HOLDOUT_SLUG + 1):
            seed = _stable_seed_for(f"impostor-holdout::{slug}", view_i)
            view = render_synthetic_views(img, n=1, seed=seed)[0]
            filename = f"{slug}__impostor-holdout-{view_i:02d}.jpg"
            (_PHOTOS_DIR / filename).write_bytes(imageio.encode_jpeg(view))
            rows.append({"filename": filename, "slug": slug, "source": "holdout_real", "seed": str(seed)})

    # --- источник 2: синтетические "выдуманные этикетки" --------------------------------
    for i in range(1, N_FAKE_LABELS + 1):
        fake_slug = f"fake-label-{i:02d}"
        texture_seed = FAKE_LABEL_SEED_BASE + i
        texture = generate_fake_label_texture(texture_seed)
        view_seed = _stable_seed_for(f"impostor-fake::{fake_slug}", 1)
        view = render_synthetic_views(texture, n=1, seed=view_seed)[0]
        filename = f"{fake_slug}__impostor-fake-01.jpg"
        (_PHOTOS_DIR / filename).write_bytes(imageio.encode_jpeg(view))
        rows.append({"filename": filename, "slug": fake_slug, "source": "fake_synthetic", "seed": str(view_seed)})

    with _LABELS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "slug", "source", "seed"])
        writer.writeheader()
        writer.writerows(rows)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "qa/gen_impostor_photos.py",
        "holdout_slugs": holdout_slugs,
        "n_calibration_refs": len(calibration_refs),
        "n_holdout_photos": sum(1 for r in rows if r["source"] == "holdout_real"),
        "n_fake_photos": sum(1 for r in rows if r["source"] == "fake_synthetic"),
        "n_total_impostor_photos": len(rows),
        "note": (
            "Impostor-холдаут для калибровки CV_CONFIDENT_SCORE_THRESHOLD / ветки "
            "not_in_catalog (ревью 04, чек-лист п.3). Два независимых источника: held-out "
            "реальные вина (физически исключены из калибровочного индекса, refs.csv) и "
            "процедурно нарисованные фейковые этикетки (никогда не были вином, ни один "
            "реальный бренд не использован). Синтетика — не полевые фото кейсодержателя."
        ),
    }
    _META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[gen-impostor] калибровочный индекс: {len(calibration_refs)} эталонов -> {_REFS_CSV}")
    print(f"[gen-impostor] held-out реальные: {sum(1 for r in rows if r['source']=='holdout_real')} фото ({len(holdout_slugs)} слагов)")
    print(f"[gen-impostor] fake-синтетика: {sum(1 for r in rows if r['source']=='fake_synthetic')} фото")
    print(f"[gen-impostor] разметка -> {_LABELS_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
