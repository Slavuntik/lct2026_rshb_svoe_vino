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


def is_acceptable_match(predicted_slug: str | None, true_slug: str) -> bool:
    if predicted_slug == true_slug:
        return True
    return any(predicted_slug in g and true_slug in g for g in NEAR_DUP_GROUPS)


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
