"""ImageIndex — контракт contracts/image-scan.md, реализация точно по сигнатурам.

Мульти-ракурсная укладка: на позицию (slug) хранится N+1 точек в Qdrant (эталон +
синтетические ракурсы аугментатора — cv/augment.py), каждая точка — отдельный вектор
с payload `{id, slug, view}`. `search()` идёт по РАКУРСАМ (ANN с оверфетчем), затем
схлопывается в ПОЗИЦИИ: контракт — "поиск возвращает лучший ракурс позиции, дубли
позиции схлопываются" — на каждый slug оставляем максимум score среди его точек.

`Match.gap` — отрыв ТОП-кандидата не от следующего по списку, а от первой позиции ВНЕ
его "группы" близких скоров (см. `_cluster_by_score`). Near-dup позиции (та же
этикетка, разные год/категория — case.md) естественно попадают в одну группу: их
эталонные фото визуально почти идентичны (иногда буквально один и тот же файл — см.
aligote-barrel-2024/2025 в devfix/manifest.json), поэтому embedding-скор их не
разделяет — и не должен: разделение этой пары — работа OCR-верификатора (contracts/
image-scan.md, "Пайплайн /scan/photo"; не входит в зону агента G). `gap` здесь —
честный сигнал "как далеко до первого визуально непохожего конкурента", а не шумный
артефакт от near-dup соседей по списку.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

import numpy as np

from cv import config, imageio
from cv.augment import prepare_reference, render_synthetic_views
from cv.encoder import SiglipEncoder
from cv.normalize import normalize_query
from cv.store import QdrantStore, get_store


@dataclass
class Match:
    slug: str
    score: float  # сравним только внутри одного ответа (как Candidate.score в packages/rag)
    gap: float | None  # отрыв от следующего кандидата НЕ из той же группы близких скоров
    view: str  # какой ракурс эталона сматчился ("real" | "synth-N")


def _cluster_by_score(sorted_scores: list[float], epsilon: float) -> list[int]:
    """Индекс группы для каждого элемента УЖЕ отсортированного по убыванию списка
    скоров. Цепная кластеризация: сосед — той же группы, если разница с ПРЕДЫДУЩИМ
    (не с первым элементом группы) не превышает `epsilon` — так длинная цепочка
    близких near-dup позиций не "расползается" из-за накопленного дрейфа."""
    groups = []
    group_id = 0
    prev = None
    for score in sorted_scores:
        if prev is not None and (prev - score) > epsilon:
            group_id += 1
        groups.append(group_id)
        prev = score
    return groups


def _gaps_to_next_group(sorted_scores: list[float], epsilon: float) -> list[float | None]:
    n = len(sorted_scores)
    groups = _cluster_by_score(sorted_scores, epsilon)
    gaps: list[float | None] = [None] * n
    for i in range(n):
        for j in range(i + 1, n):
            if groups[j] != groups[i]:
                gaps[i] = sorted_scores[i] - sorted_scores[j]
                break
    return gaps


class ImageIndex:
    def __init__(
        self,
        store: QdrantStore | None = None,
        encoder: SiglipEncoder | None = None,
        collection: str | None = None,
    ):
        self.store = store or get_store()
        self.encoder = encoder or SiglipEncoder()
        self.collection = collection or config.COLLECTION_NAME
        self._manifest_path = config.MANIFEST_PATH

    # --- контракт ----------------------------------------------------------------

    def embed(self, image: bytes) -> list[float]:
        """Эмбеддинг БЕЗ нормализации — сырое декодирование + энкодер. `search()`
        сам вызывает нормализацию до эмбеддинга; `embed()` — для случаев, где вызывающий
        код уже подготовил изображение сам (или намеренно хочет вектор "как есть")."""
        arr = imageio.decode_image(image)  # ValueError на битые байты
        return self.encoder.encode(arr)

    def search(self, image: bytes, top_k: int = 5, *, normalize: bool = True) -> list[Match]:
        """Запрос проходит нормализацию ДО эмбеддинга (контракт): детект этикетки →
        кроп → развёртка цилиндра → фотометрия. `normalize=False` — явный флаг
        отключения для A/B в eval (единственный легитимный способ пропустить её)."""
        arr = imageio.decode_image(image)  # ValueError на битые байты — до любой другой работы
        prepared = normalize_query(arr, enabled=normalize)
        vector = self.encoder.encode(prepared)

        # ANN идёt по РАКУРСАМ, не по позициям — оверфетчим, иначе после схлопывания
        # на выходе может остаться меньше top_k уникальных slug'ов, чем попросили.
        overfetch = max(top_k * config.SEARCH_OVERFETCH, top_k + 10)
        raw = self.store.search(self.collection, vector, top_k=overfetch)

        best_by_slug: dict[str, tuple[float, str]] = {}
        for _point_id, score, payload in raw:
            slug = payload.get("slug")
            if slug is None:
                continue
            view = payload.get("view", "?")
            cur = best_by_slug.get(slug)
            if cur is None or score > cur[0]:
                best_by_slug[slug] = (score, view)

        ranked = sorted(best_by_slug.items(), key=lambda kv: kv[1][0], reverse=True)
        gaps = _gaps_to_next_group([s for _slug, (s, _v) in ranked], config.GROUP_EPSILON)

        return [
            Match(slug=slug, score=score, gap=gaps[i], view=view)
            for i, (slug, (score, view)) in enumerate(ranked[:top_k])
        ]

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        """slug -> список путей [эталон, synth-1, synth-2, ...] (эталон + синтетические
        ракурсы, контракт). Порядок в списке — конвенция: первый элемент = "real",
        остальные = "synth-N" по позиции — именно так `cv.augment.save_synthetic_views`
        пишет файлы на диск (см. докстринг там). Полная переиндексация: коллекция
        пересоздаётся с нуля (в отличие от `add()`, который апсертит поверх)."""
        dim = self.encoder.dim
        self.store.recreate_collection(self.collection, dim)

        ids, vectors, payloads = [], [], []
        for slug, paths in refs.items():
            for i, path in enumerate(paths):
                view = "real" if i == 0 else f"synth-{i}"
                arr = imageio.load_image_file(path)
                # ВАЖНО (см. reports/g-report.md, "Предположения" — нашёл на self-match):
                # и "real", и "synth" ракурсы прогоняются через ОДИНАКОВЫЙ normalize_query()
                # — тот же пайплайн, что search() применяет к запросу. Ранняя версия
                # применяла нормализацию только к "real" (через prepare_reference), а
                # "synth"-файлы (уже отрендеренные cv.augment.save_synthetic_views)
                # заносила в индекс СЫРЫМИ — из-за этого запрос (после normalize_query)
                # и его ближайшие соседи в индексе (synth-ракурсы той же позиции, БЕЗ
                # normalize_query) жили в разных "визуальных доменах", и self-match
                # проседал именно от этого рассинхрона, а не от качества самой
                # нормализации как таковой (ablation подтвердил: рассинхрон устранён —
                # цифры восстановились, см. отчёт).
                view_arr = prepare_reference(arr) if i == 0 else normalize_query(arr, enabled=True)
                vec = self.encoder.encode(view_arr)
                ids.append(f"{slug}::{view}")
                vectors.append(vec)
                payloads.append({"slug": slug, "view": view})
        self.store.upsert(self.collection, ids, vectors, payloads)

        manifest = {
            "version": version,
            "model": self.encoder.model_name,
            "device": self.encoder.device,
            "dim": dim,
            "positions": len(refs),
            "vectors": len(ids),
            "built_at": time.time(),
        }
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, slug: str, images: list[bytes]) -> None:
        """+N позиций/день без ребилда (контракт).

        ПРЕДПОЛОЖЕНИЕ (контракт не уточняет — см. reports/g-report.md,
        "Предположения"): каждый элемент `images` — САМ ЭТАЛОН новой позиции (в
        духе case.md "по одной эталонной фотографии на позицию" — типично длина
        списка 1). `add()` сам прогоняет аугментатор на каждом переданном эталоне,
        как `build()` делает для основного индекса, и апсертит точки в ТУ ЖЕ
        коллекцию — инкрементальный upsert Qdrant не требует пересборки остального
        индекса (коллекция создаётся, только если её ещё не было — `ensure_collection`).
        """
        ids, vectors, payloads = [], [], []
        for img_idx, image_bytes in enumerate(images):
            arr = imageio.decode_image(image_bytes)  # ValueError на битые байты
            suffix = "" if img_idx == 0 else f"-{img_idx}"

            real_view = f"real{suffix}"
            real_vec = self.encoder.encode(prepare_reference(arr))
            ids.append(f"{slug}::{real_view}")
            vectors.append(real_vec)
            payloads.append({"slug": slug, "view": real_view})

            synths = render_synthetic_views(arr, n=config.AUGMENT_VIEWS_PER_REF, seed=config.AUGMENT_SEED_DEFAULT)
            for i, view_arr in enumerate(synths, start=1):
                view = f"synth-{i}{suffix}"
                # Тот же normalize_query(), что build() и search() — см. комментарий в build().
                vec = self.encoder.encode(normalize_query(view_arr, enabled=True))
                ids.append(f"{slug}::{view}")
                vectors.append(vec)
                payloads.append({"slug": slug, "view": view})

        if not vectors:
            return
        self.store.ensure_collection(self.collection, len(vectors[0]))
        self.store.upsert(self.collection, ids, vectors, payloads)
