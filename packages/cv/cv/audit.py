"""Диагностика качества эталонов каталога (cv/audit.py) — G3, agents/G3-real-index.md,
дополнение оркестратора от 16.09.2026: "среди эталонов кейса много шума — лайфстайл-фото
(человек с бокалом), виноградники, интерьеры вместо снимка бутылки".

Классический детектор силуэта (`cv.normalize._foreground_bbox`, используется
`detect_label_region()`) не находит контрастную "бутылку на фоне" на таком фото и
откатывается на fallback-кроп всего кадра (`cv.normalize._fallback_region`) — дешёвый
(не нейросетевой, миллисекунды на фото) прокси-сигнал "похоже на шум", НЕ точная
классификация (F3 делает настоящий семантический триаж отдельно, этот сигнал — только
чтобы не размазывать self-match/gap цифры шумными позициями и передать F3 список
кандидатов на сверку).

Отдельный проход, не часть `ImageIndex.build()` — читает те же файлы-эталоны, что и
build (не эмбеддинги из `.embed_cache`: fallback — свойство ИСХОДНОГО кадра, кэш
эмбеддингов его не хранит), но не трогает энкодер/Qdrant, поэтому можно прогнать и до,
и после долгой сборки индекса без пересборки ("не перезапускай ради логов, добери их
отдельным проходом" — оркестратор)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from cv import imageio
from cv.normalize import detector_used_fallback

AUDIT_PROGRESS_EVERY = 300


def label_detector_outcomes(primary_refs: dict[str, Path], *, verbose: bool = True) -> dict:
    """slug -> путь к ЭТАЛОНУ (первый файл слага, НЕ синтетические ракурсы — синтетика
    аугментатора всегда "бутылкообразна" по построению by design, диагностика имеет
    смысл только на сырых фото каталога). Возвращает агрегат + `per_slug`/`fallback_slugs`
    для джойна с self-match/gap-отчётами (см. agents/G3-real-index.md, п.3 дополнения)."""
    per_slug: dict[str, bool] = {}
    unreadable: list[str] = []
    t0 = time.perf_counter()
    for i, (slug, path) in enumerate(primary_refs.items(), start=1):
        try:
            arr = imageio.load_image_file(str(path))
        except ValueError:
            unreadable.append(slug)
            continue
        per_slug[slug] = detector_used_fallback(arr)
        if verbose and i % AUDIT_PROGRESS_EVERY == 0:
            elapsed = time.perf_counter() - t0
            print(f"[audit-refs] {i}/{len(primary_refs)} эталонов, {elapsed:.0f} с", file=sys.stderr)

    fallback_slugs = sorted(s for s, fb in per_slug.items() if fb)
    total = len(per_slug)
    return {
        "total": total,
        "fallback_count": len(fallback_slugs),
        "fallback_rate": round(len(fallback_slugs) / total, 4) if total else 0.0,
        "fallback_slugs": fallback_slugs,
        "unreadable_slugs": unreadable,
        "per_slug": per_slug,
    }
