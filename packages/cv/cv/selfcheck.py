"""Селфчек: self-match top-1 аугментированных ракурсов на построенном индексе.

DoD (бриф/контракт): аугментация эталона находит свой slug top-1 на индексе фикстур
в >= 90% случаев. Ключевая методологическая деталь: селфчек рендерит СВЕЖИЕ ракурсы
с ДРУГИМ seed, чем при `build()` (см. `HOLDOUT_SEED_OFFSET`) — если бы мы просто
переиспользовали ракурсы, уже лежащие в индексе, self-match был бы тривиален (тот же
вектор находит сам себя). Свежий seed честно проверяет, что аугментатор+нормализация+
энкодер ОБОБЩАЮТСЯ на новую случайную реализацию тех же диапазонов искажений, а не
просто помнят конкретные векторы.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from cv import config, imageio
from cv.augment import render_synthetic_views
from cv.index import ImageIndex

HOLDOUT_SEED_OFFSET = 9973  # простое число — заведомо не совпадает с seed(ами) сборки
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Известные near-dup группы дев-фикстур: одна и та же этикетка (иногда буквально
# один файл — см. devfix/manifest.json), разные год/категория в каталоге. Контракт
# прямо говорит, что различать их — работа OCR-верификатора, НЕ CV (agents/G-cv.md,
# "near-dup: пара 2024/2025 попадает в одну группу кандидатов, различать будет OCR —
# не ты"). Self-match top-1 внутри одной группы — ожидаемое поведение классического
# визуального поиска (эмбеддинги двух буквально идентичных фото неотличимы), не
# ошибка модели — считаем его успехом, а не провалом (иначе метрика штрафует за то,
# что CV структурно не может знать). Пары общего вида (не только эта) — вне бюджета
# v1 без датасета кейса; список расширяется по мере появления новых known-групп.
NEAR_DUP_GROUPS: list[frozenset[str]] = [
    frozenset({"aligote-barrel-2024", "aligote-barrel-2025"}),
]


def is_acceptable_match(
    predicted_slug: str | None, true_slug: str, groups: list[frozenset[str]] | None = None
) -> bool:
    """`groups` по умолчанию — известные near-dup пары дев-фикстур (`NEAR_DUP_GROUPS`);
    G3 (agents/G3-real-index.md) передаёт сюда реальные семьи каталога кейса
    (`case-data/slug_refs.json["families"]`) — параметр, а не переопределение
    глобала, чтобы дев-фикстурный self-match (`run_selfcheck`) не зависел от того,
    что лежит в датасете кейса на момент запуска."""
    if predicted_slug == true_slug:
        return True
    g = groups if groups is not None else NEAR_DUP_GROUPS
    return any(predicted_slug in grp and true_slug in grp for grp in g)


def run_selfcheck(
    fixtures_dir: Path,
    index: ImageIndex | None = None,
    n_views: int = 5,
    seed: int = config.AUGMENT_SEED_DEFAULT,
    top_k: int = 5,
) -> dict:
    index = index or ImageIndex()
    paths = sorted(p for p in Path(fixtures_dir).iterdir() if p.suffix.lower() in IMAGE_EXTS)

    total = 0
    hits = 0
    details = []
    for p in paths:
        slug = p.stem
        try:
            arr = imageio.load_image_file(str(p))
        except ValueError:
            continue
        holdout_views = render_synthetic_views(arr, n=n_views, seed=seed + HOLDOUT_SEED_OFFSET)
        for view_i, view in enumerate(holdout_views):
            total += 1
            matches = index.search(imageio.encode_jpeg(view), top_k=top_k)
            top1 = matches[0].slug if matches else None
            ok = is_acceptable_match(top1, slug)
            hits += int(ok)
            details.append({"slug": slug, "view_i": view_i, "top1": top1, "ok": ok})

    rate = hits / total if total else 0.0
    return {"total": total, "hits": hits, "top1_rate": round(rate, 4), "details": details}


def _rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def run_selfcheck_from_refs(
    refs: dict[str, Path],
    index: ImageIndex | None = None,
    n_views: int = 2,
    seed: int = config.AUGMENT_SEED_DEFAULT,
    top_k: int = 5,
    sample_n: int | None = None,
    sample_seed: int = 0,
    near_dup_groups: list[frozenset[str]] | None = None,
    fallback_slugs: set[str] | None = None,
) -> dict:
    """Self-match на БОЕВОМ индексе (agents/G3-real-index.md п.4): `refs` — slug ->
    путь к эталонному фото из `case-data/slug_refs.json` (первый файл слага; см.
    `cv.cli.discover_refs_from_slug_refs_json`), НЕ дев-директория `run_selfcheck`
    выше — там slug выводится из ИМЕНИ ФАЙЛА (`p.stem`), что не годится для боевых
    данных: имена файлов в `uploads/` произвольные хэши/оригинальные имена, не slug.

    Сэмплирует `sample_n` слагов детерминированно по `sample_seed` (без `n_views` х
    ~1700 позиций на каждый прогон — бриф просит "сэмпл >= 300 слагов", не полный
    прогон), рендерит `n_views` свежих holdout-ракурсов (см. `HOLDOUT_SEED_OFFSET`
    выше — другой seed, чем любой `build()`/`add()`, иначе self-match тривиален) и
    считает top-1 И top-5 rate (бриф п.4 просит оба — `run_selfcheck` выше исторически
    даёт только top-1, здесь оба ради боевого отчёта, без изменения старой функции).

    `near_dup_groups` — реальные near-dup семьи каталога (`slug_refs.json["families"]`,
    как frozenset per семья), не дев-фикстурная `NEAR_DUP_GROUPS` — см. docstring
    `is_acceptable_match`.

    `fallback_slugs` — множество слагов, чей ЭТАЛОН дал fallback у детектора этикетки
    (`cv.audit.label_detector_outcomes`, дополнение оркестратора от 16.09.2026: "шумные
    эталоны размажут цифры и мы примем болезнь за норму"). Если передано — отчёт несёт
    `by_ref_quality: {clean, fallback}` с отдельными top1/top5 для каждой группы, поверх
    общих цифр (которые остаются как есть, ничего не выбрасывается из общего счёта)."""
    index = index or ImageIndex()
    fallback_slugs = fallback_slugs or set()

    slugs = sorted(refs.keys())
    if sample_n is not None and sample_n < len(slugs):
        rng = np.random.default_rng(sample_seed)
        idx = rng.choice(len(slugs), size=sample_n, replace=False)
        slugs = sorted(slugs[i] for i in idx)

    total = top1_hits = top5_hits = 0
    by_quality = {
        "clean": {"total": 0, "top1_hits": 0, "top5_hits": 0},
        "fallback": {"total": 0, "top1_hits": 0, "top5_hits": 0},
    }
    details = []
    for slug in slugs:
        path = refs[slug]
        try:
            arr = imageio.load_image_file(str(path))
        except ValueError:
            continue
        is_fallback_ref = slug in fallback_slugs
        bucket = by_quality["fallback"] if is_fallback_ref else by_quality["clean"]
        holdout_views = render_synthetic_views(arr, n=n_views, seed=seed + HOLDOUT_SEED_OFFSET)
        for view_i, view in enumerate(holdout_views):
            total += 1
            bucket["total"] += 1
            matches = index.search(imageio.encode_jpeg(view), top_k=top_k)
            top1 = matches[0].slug if matches else None
            ok1 = is_acceptable_match(top1, slug, near_dup_groups)
            ok5 = any(is_acceptable_match(m.slug, slug, near_dup_groups) for m in matches)
            top1_hits += int(ok1)
            top5_hits += int(ok5)
            bucket["top1_hits"] += int(ok1)
            bucket["top5_hits"] += int(ok5)
            details.append(
                {
                    "slug": slug,
                    "view_i": view_i,
                    "top1": top1,
                    "ok1": ok1,
                    "ok5": ok5,
                    "fallback_ref": is_fallback_ref,
                }
            )

    report = {
        "sampled_slugs": len(slugs),
        "total_views": total,
        "top1_hits": top1_hits,
        "top1_rate": _rate(top1_hits, total),
        "top5_hits": top5_hits,
        "top5_rate": _rate(top5_hits, total),
        "details": details,
    }
    if fallback_slugs:
        report["by_ref_quality"] = {
            name: {
                "total": b["total"],
                "top1_rate": _rate(b["top1_hits"], b["total"]),
                "top5_rate": _rate(b["top5_hits"], b["total"]),
            }
            for name, b in by_quality.items()
        }
    return report
