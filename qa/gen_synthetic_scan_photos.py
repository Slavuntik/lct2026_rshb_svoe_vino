#!/usr/bin/env python3
"""qa/gen_synthetic_scan_photos.py — генератор синтетических «полевых» фото сканера
(агент F2, продолжение зоны F после обрыва сессии — см. reports/f-report.md).

Генерирует 30-50 синтетических фото «у полки, под углом» по мотивам дев-фикстур
packages/cv/devfix (56 эталонов, агент G) — используя ЕГО аугментатор
(cv/augment.py::render_synthetic_views) как READ-ONLY API. Ничего не пишет и не меняет
в packages/cv/ (зона G) — только читает эталоны из devfix/.

Ключевая методологическая деталь (тот же приём, что packages/cv/cv/selfcheck.py
использует для self-match и packages/cv/cv/cli.py::cmd_bench — для тайминга): каждое
"полевое" фото рендерится на СВОЁМ seed из домена FIELD_SEED_BASE, заведомо отличного от
build-seed индекса (0 по умолчанию, cv/config.py::AUGMENT_SEED_DEFAULT) и от
holdout-offset селфчека G (9973, cv/selfcheck.py::HOLDOUT_SEED_OFFSET). Так фото честно
проверяют ОБОБЩЕНИЕ пайплайна на новую случайную реализацию искажений, а не совпадают
побайтово с одним из ракурсов, уже лежащих в индексе.

Запускать ТОЛЬКО через venv пакета cv — там есть numpy/torch/opencv/pillow, которых нет в
qa/.venv (qa/requirements.txt: только pytest/pyyaml/playwright):

    packages/cv/.venv/bin/python qa/gen_synthetic_scan_photos.py

Пишет (всё — в свою зону qa/synthetic/, добавлено в qa/.gitignore):
    qa/synthetic/photos/<slug>__field-NN.jpg   — сами фото
    qa/synthetic/labels.csv                    — разметка (колонки узнаёт qa/scan_eval.py
                                                  по алиасам заголовков: filename/slug)
    qa/synthetic/GENERATION.json               — параметры прогона, для воспроизводимости

Модуль импортируем ЛЕНИВО внутри main() (не на уровне модуля) — тяжёлые cv.* импорты
(torch/opencv) нужны только для реального прогона; чистая логика планирования
(discover_devfix_slugs, build_generation_plan, _stable_seed_for) не тянет их и потому
юнит-тестируется быстро под qa/.venv (см. qa/test_synthetic_baseline.py).
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_DEVFIX_DIR = _REPO_ROOT / "packages" / "cv" / "devfix"
_OUT_DIR = _QA_DIR / "synthetic"
_PHOTOS_DIR = _OUT_DIR / "photos"
_LABELS_CSV = _OUT_DIR / "labels.csv"
_META_JSON = _OUT_DIR / "GENERATION.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Отдельный "полевой" домен seed'ов — произвольное, но зафиксированное число; важна не
# конкретная величина, а то, что она не совпадает ни с build-seed (0), ни с
# selfcheck-offset G (9973).
FIELD_SEED_BASE = 424242

N_BASE_SLUGS = 42  # столько разных вин получают ровно одно «полевое» фото
# + независимый второй ракурс для этих (near-dup пара — намеренно, case.md: "главный
# источник ошибок"; "kaberne-sovinon" — произвольный третий, для проверки consistency
# в целом, не только на near-dup паре).
EXTRA_VIEW_SLUGS = ["aligote-barrel-2024", "aligote-barrel-2025", "kaberne-sovinon"]
TOTAL_EXPECTED = N_BASE_SLUGS + len(EXTRA_VIEW_SLUGS)  # 45 — внутри диапазона задания 30-50


def _stable_seed_for(slug: str, view_index: int) -> int:
    """Детерминированный seed для (slug, view_index) — sha256 (НЕ встроенный `hash()`,
    который рандомизирован между процессами без PYTHONHASHSEED, см. предостережение в
    cv/cli.py::cmd_bench, тот же самый паттерн `hash(p.name)`)."""
    digest = hashlib.sha256(f"{FIELD_SEED_BASE}:{slug}:{view_index}".encode("utf-8")).hexdigest()
    return FIELD_SEED_BASE + (int(digest[:8], 16) % 1_000_000)


def discover_devfix_slugs(devfix_dir: Path) -> list[str]:
    """slug = имя файла без расширения, отсортировано — детерминированный порядок отбора."""
    return sorted(p.stem for p in devfix_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def build_generation_plan(all_slugs: list[str]) -> list[tuple[str, int]]:
    """(slug, view_index) для каждого «полевого» фото. Чистая функция (без I/O) —
    юнит-тестируется отдельно от рендеринга. `view_index` начинается с 1; slug получает
    view_index=2, только если он и так уже входит в первые N_BASE_SLUGS (иначе он получил
    бы фото "с дырой" — только 2-й ракурс без 1-го)."""
    base_slugs = all_slugs[:N_BASE_SLUGS]
    base_set = set(base_slugs)
    plan: list[tuple[str, int]] = [(slug, 1) for slug in base_slugs]
    seen_extra: set[str] = set()
    for slug in EXTRA_VIEW_SLUGS:
        if slug not in all_slugs or slug in seen_extra:
            continue
        seen_extra.add(slug)
        view_index = 2 if slug in base_set else 1
        plan.append((slug, view_index))
    return plan


def _find_ref_file(devfix_dir: Path, slug: str) -> Path | None:
    direct = devfix_dir / f"{slug}.webp"
    if direct.is_file():
        return direct
    candidates = [c for c in devfix_dir.glob(f"{slug}.*") if c.suffix.lower() in IMAGE_EXTS]
    return candidates[0] if candidates else None


def main() -> int:
    from cv import imageio  # лениво: тяжёлые зависимости нужны только для реального прогона
    from cv.augment import render_synthetic_views

    if not _DEVFIX_DIR.is_dir():
        print(f"devfix не найден: {_DEVFIX_DIR}", file=sys.stderr)
        return 2

    all_slugs = discover_devfix_slugs(_DEVFIX_DIR)
    if len(all_slugs) < N_BASE_SLUGS:
        print(f"в devfix только {len(all_slugs)} фото, нужно >= {N_BASE_SLUGS}", file=sys.stderr)
        return 2

    plan = build_generation_plan(all_slugs)

    _PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for slug, view_index in plan:
        src = _find_ref_file(_DEVFIX_DIR, slug)
        if src is None:
            print(f"эталон для {slug!r} не найден — пропущен", file=sys.stderr)
            continue
        seed = _stable_seed_for(slug, view_index)
        img = imageio.load_image_file(str(src))
        view = render_synthetic_views(img, n=1, seed=seed)[0]
        filename = f"{slug}__field-{view_index:02d}.jpg"
        out_path = _PHOTOS_DIR / filename
        out_path.write_bytes(imageio.encode_jpeg(view))
        rows.append({"filename": filename, "slug": slug, "seed": str(seed), "source_ref": src.name})

    with _LABELS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["filename", "slug", "seed", "source_ref"])
        writer.writeheader()
        writer.writerows(rows)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "qa/gen_synthetic_scan_photos.py",
        "augmentor": "packages/cv/cv/augment.py::render_synthetic_views (read-only вызов, cv не изменён)",
        "field_seed_base": FIELD_SEED_BASE,
        "n_photos": len(rows),
        "n_distinct_slugs": len(set(r["slug"] for r in rows)),
        "extra_view_slugs": EXTRA_VIEW_SLUGS,
        "devfix_dir": str(_DEVFIX_DIR),
        "note": (
            "Синтетика, НЕ полевые фото кейсодержателя (case.md: два реальных набора "
            "приезжают отдельно). Seed-домен генерации намеренно отличается от build-seed "
            "индекса (0) и от selfcheck holdout-offset G (9973) — каждое фото здесь честный "
            "новый ракурс, не переиспользование вектора, уже лежащего в индексе."
        ),
    }
    _META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[gen] сгенерировано {len(rows)} фото ({len(set(r['slug'] for r in rows))} уникальных slug) -> {_PHOTOS_DIR}")
    print(f"[gen] разметка -> {_LABELS_CSV}")
    print(f"[gen] метаданные -> {_META_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
