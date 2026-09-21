"""ImageIndex — контракт contracts/image-scan.md, реализация точно по сигнатурам.

Мульти-ракурсная укладка: на позицию (slug) хранится N+1 точек в Qdrant (эталон +
синтетические ракурсы аугментатора — cv/augment.py), каждая точка — отдельный вектор
с payload `{id, slug, view}`. `search()` идёт по РАКУРСАМ (ANN с оверфетчем), затем
схлопывается в ПОЗИЦИИ: контракт — "поиск возвращает лучший ракурс позиции, дубли
позиции схлопываются" — на каждый slug оставляем максимум score среди его точек.

`Match.gap` — отрыв ТОП-кандидата не от следующего по списку, а от первой позиции ВНЕ
его "группы" (см. `_gaps_to_next_family`/`_cluster_by_score`). Near-dup позиции (та же
этикетка, разные год/категория — case.md) естественно попадают в одну группу: их
эталонные фото визуально почти идентичны (иногда буквально один и тот же файл — см.
aligote-barrel-2024/2025 в devfix/manifest.json), поэтому embedding-скор их не
разделяет — и не должен: разделение этой пары — работа OCR-верификатора (contracts/
image-scan.md, "Пайплайн /scan/photo"; не входит в зону агента G). `gap` здесь —
честный сигнал "как далеко до первого визуально непохожего конкурента", а не шумный
артефакт от near-dup соседей по списку.

v0.4.7 п.1 (agents/G4-family-gap.md, TODO-0 ревью 05): "группа" ТЕПЕРЬ определяется
ПЕРЕПИСЬЮ near-dup семей кейса (`case-data/families.json`, F3 — см. `cv/families.py`),
не эпсилон-цепочкой скоров — эпсилон-кластеризация (`_cluster_by_score`/
`_gaps_to_next_group`, CV_GROUP_EPSILON) остаётся ТОЛЬКО фолбэком, когда переписи нет
(файл отсутствует/пуст/не задан env `CV_FAMILIES_JSON`). Находка ночной волны
(reviews/05-dataset-wave.md, TODO-0): на плотном каталоге (1982 визуально похожих
вина) эпсилон-цепочка почти никогда не находит разрыв даже за много кандидатов —
`gap` был `null` почти всегда, включая случаи, где top-1 явно "чужой" (не из ЧЕСТНОЙ
near-dup семьи top-1, просто визуально похож на плотном каталоге). Семья по переписи
не страдает от этого — конкурент ищется по факту членства, не по гладкости убывания
скора.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from qdrant_client.models import FieldCondition, Filter, MatchAny

from cv import config, families, imageio
from cv.augment import prepare_reference, render_synthetic_views
from cv.encoder import SiglipEncoder
from cv.normalize import normalize_query
from cv.store import QdrantStore, get_store

# Печать прогресса в build() каждые N векторов (G3: боевая сборка ~35-44 тыс.
# векторов идёт минуты — "запускай фоном, следи за прогрессом", agents/G3-real-index.md
# — молчаливый долгий процесс неотличим от зависшего).
_BUILD_PROGRESS_EVERY = 500


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


def _gaps_to_next_family(
    sorted_slugs: list[str], sorted_scores: list[float], family_by_slug: dict[str, str]
) -> list[float | None]:
    """v0.4.7 п.1: `gap[i]` = score[i] - score первого j>i, чей slug НЕ принадлежит
    той же near-dup СЕМЬЕ переписи, что slug[i] (`family_by_slug`, см. `cv/families.py`).

    Слаг без записи в переписи — семья "из одного себя": ЛЮБОЙ другой кандидат
    (зарегистрированный в семье или нет) считается конкурентом, поэтому gap ищется
    уже на следующем ранге. Это осознанно НЕ то же самое, что "непохожий скор" —
    перепись может (и в кейсе часто будет) содержать соседей с почти идентичным
    скором ВНЕ семьи top-1 (плотный каталог, TODO-0 ревью 05): семья по переписи —
    факт курации F3, не производная от гладкости кривой скора."""
    n = len(sorted_slugs)
    gaps: list[float | None] = [None] * n
    for i in range(n):
        family_i = family_by_slug.get(sorted_slugs[i])
        for j in range(i + 1, n):
            family_j = family_by_slug.get(sorted_slugs[j])
            same_family = family_i is not None and family_i == family_j
            if not same_family:
                gaps[i] = sorted_scores[i] - sorted_scores[j]
                break
    return gaps


class ImageIndex:
    def __init__(
        self,
        store: QdrantStore | None = None,
        encoder: SiglipEncoder | None = None,
        collection: str | None = None,
        manifest_path: Path | None = None,
        families_json: Path | None = None,
    ):
        self.store = store or get_store()
        self.encoder = encoder or SiglipEncoder()
        self.collection = collection or config.COLLECTION_NAME
        # Манифест по умолчанию — РЯДОМ С ДАННЫМИ ЭТОГО КОНКРЕТНОГО store (self.store.path),
        # не отдельная глобальная config.MANIFEST_PATH. Та резолвится как модульная константа
        # ОДИН РАЗ при первом импорте cv.config — не видит env/аргументы, выставленные ПОСЛЕ
        # импорта; в процессе с несколькими ImageIndex на разные store (изолированные тесты,
        # инструмент, что угодно, создающее не один индекс за раз) это читало ЧУЖОЙ манифест
        # (находка B при интеграции, ревью 04+). `self.store.path` резолвится лениво, в момент
        # конструирования КОНКРЕТНОГО QdrantStore — существует всегда (даже в сетевом режиме,
        # где физически не используется для хранения векторов), так что это безопасная и
        # всегда доступная привязка "эта позиция на диске -> её манифест". Явный параметр
        # `manifest_path` по-прежнему в приоритете, если передан.
        self._manifest_path = manifest_path or (self.store.path / "manifest.json")
        # Тайминги последнего build() по стадиям (load/normalize/embed/upsert) — не
        # часть контракта, читается по желанию вызывающим кодом (CLI `cv build-index`,
        # G3: agents/G3-real-index.md п.2 просит замер стадий боевой сборки). None до
        # первого build().
        self.last_build_stats: dict | None = None
        # v0.4.7 п.1: near-dup семьи переписи для Match.gap — путь запоминаем как есть
        # (может быть None -> резолвится лениво), сам словарь slug->family_id грузится
        # ОДИН РАЗ при первом search() (см. _get_family_by_slug), не здесь — контракт
        # брифа "семьи грузятся один раз лениво" и симметрия с LabelVerifier._ocr.
        self._families_json = families_json
        self._family_by_slug: dict[str, str] | None = None

    @property
    def index_version(self) -> str | None:
        """Версия последнего `build()` из манифеста (ревью 04, блокер 5: `/v1/metrics/scan`
        у B обязан показывать настоящую версию индекса, не env-плейсхолдер). `None`, если
        манифеста ещё нет (индекс не строился) — вызывающий код решает, как это показать,
        не наша забота выдумывать значение по умолчанию."""
        if not self._manifest_path.exists():
            return None
        try:
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        version = manifest.get("version")
        return version if isinstance(version, str) else None

    def _get_family_by_slug(self) -> dict[str, str]:
        """v0.4.7 п.1: near-dup семьи переписи, `slug -> family_id` (`cv/families.py`).
        Лениво — путь резолвится ЗДЕСЬ, в момент первого вызова (не в `__init__`), так
        `CV_FAMILIES_JSON`/`CASE_DATA_DIR`, выставленные ПОСЛЕ конструирования
        `ImageIndex` (типичный порядок в тестах: создать индекс -> настроить env ->
        искать), всё равно видны — тот же принцип отложенного резолва, что и у
        `LabelVerifier._load()` для PaddleOCR. Результат кэшируется на инстансе
        (`self._family_by_slug`) — файл переписи (сотни KB) не перечитывается на
        каждый `search()`."""
        if self._family_by_slug is None:
            path = self._families_json or families.default_families_path()
            self._family_by_slug = families.load_family_by_slug(path)
        return self._family_by_slug

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
        scores_ranked = [s for _slug, (s, _v) in ranked]

        # v0.4.7 п.1: конкурент для gap — первый кандидат НЕ из семьи top-1 по
        # переписи (case-data/families.json), не первый скор-разрыв > эпсилона.
        # Эпсилон-группировка (CV_GROUP_EPSILON) — ТОЛЬКО фолбэк, когда переписи
        # нет (файл отсутствует/пуст/CV_FAMILIES_JSON не задан и дефолтного файла
        # тоже нет) — контракт "ничего не ломая", прежнее поведение сохранено 1:1.
        family_by_slug = self._get_family_by_slug()
        if family_by_slug:
            gaps = _gaps_to_next_family([slug for slug, _ in ranked], scores_ranked, family_by_slug)
        else:
            gaps = _gaps_to_next_group(scores_ranked, config.GROUP_EPSILON)

        return [
            Match(slug=slug, score=score, gap=gaps[i], view=view)
            for i, (slug, (score, view)) in enumerate(ranked[:top_k])
        ]

    # --- CV_FUSION (agents/G7-text-fusion.md) --------------------------------------

    def embed_fusion_query(self, image: bytes, *, normalize: bool = True) -> tuple[list[float], list[float]]:
        """Два query-вектора слияния — (нормализованный кроп этикетки, весь кадр) — БЕЗ поиска.
        Отдельно от `search_fusion()`, чтобы вызывающий код мог считать эмбеддинги
        параллельно с ожиданием текста этикетки (VLM/OCR) и затем передать их в
        `search_fusion(vectors=...)` — без повторного кодирования. `ValueError` на битые байты."""
        arr = imageio.decode_image(image)
        return self.encoder.encode(normalize_query(arr, enabled=normalize)), self.encoder.encode(arr)

    def search_fusion(
        self,
        image: bytes | None,
        *,
        top_k: int = 50,
        extra_slugs: Iterable[str] = (),
        normalize: bool = True,
        vectors: tuple[list[float], list[float]] | None = None,
    ) -> list[Match]:
        """CV-скоры для боевого слияния CV+текст (`cv/text_fusion.py`, brief п.5):
        максимум по ДВУМ входам запроса — нормализованный кроп этикетки (как
        `search()`) И весь кадр БЕЗ нормализации (сырой decode, letterbox/детектор
        не участвуют — `encoder.encode()` сам делает препроцессинг под модель, см.
        `cv/encoder.py`) — на реальных фото детектор этикетки часто берёт не то
        (блики, ракурс, теснота полки), а весь кадр иногда несёт больше сигнала.

        Кандидаты на выходе — ОБЪЕДИНЕНИЕ: (а) top-`top_k` схлопнутых позиций по
        ЭТОМУ комбинированному скору (те же ANN-оверфетч/схлопывание/gap, что
        `search()`, просто по двум запросам разом) и (б) `extra_slugs` — точным
        ФИЛЬТРОВАННЫМ запросом к Qdrant (payload `slug`, `MatchAny`), не оценкой
        через ANN-топ: текстовые кандидаты вне CV top-K (brief п.1 — "верного вина
        часто нет в CV top-10") иначе были бы вообще не видны слиянию. Slug, у
        которого нет ни одной точки в коллекции вовсе (нет эталона в индексе) —
        просто отсутствует на выходе; заглушка `cv_top1-CV_PAD` для таких слагов —
        забота вызывающего кода (`cv.text_fusion.fuse()`), не эта функция (та
        честно возвращает только то, что реально нашла).

        `gap` считается ТЕМ ЖЕ способом, что `search()` (семьи переписи/эпсилон-
        фолбэк) — на случай, если вызывающему коду нужен обычный CV-ranking по
        этому комбинированному скору без текста вовсе."""
        if vectors is not None:  # уже посчитаны `embed_fusion_query()` (параллельно с чтением текста)
            norm_vec, raw_vec = vectors
        else:
            if image is None:
                raise ValueError("search_fusion: нужны либо байты изображения, либо vectors")
            norm_vec, raw_vec = self.embed_fusion_query(image, normalize=normalize)

        combined: dict[str, tuple[float, str]] = {}
        overfetch = max(top_k * config.SEARCH_OVERFETCH, top_k + 10)
        for vector in (norm_vec, raw_vec):
            raw = self.store.search(self.collection, vector, top_k=overfetch)
            for _point_id, score, payload in raw:
                slug = payload.get("slug")
                if slug is None:
                    continue
                view = payload.get("view", "?")
                cur = combined.get(slug)
                if cur is None or score > cur[0]:
                    combined[slug] = (score, view)

        ann_ranked = sorted(combined.items(), key=lambda kv: kv[1][0], reverse=True)
        keep = {slug for slug, _ in ann_ranked[:top_k]}

        missing = [s for s in dict.fromkeys(extra_slugs) if s not in combined]
        if missing:
            exact = self._exact_scores_for_slugs(missing, (norm_vec, raw_vec))
            combined.update(exact)
            keep |= set(exact)
        keep |= {s for s in extra_slugs if s in combined}

        ranked = sorted(((s, v) for s, v in combined.items() if s in keep), key=lambda kv: kv[1][0], reverse=True)
        slugs_ranked = [s for s, _ in ranked]
        scores_ranked = [v[0] for _, v in ranked]

        family_by_slug = self._get_family_by_slug()
        if family_by_slug:
            gaps = _gaps_to_next_family(slugs_ranked, scores_ranked, family_by_slug)
        else:
            gaps = _gaps_to_next_group(scores_ranked, config.GROUP_EPSILON)

        return [
            Match(slug=slug, score=score, gap=gaps[i], view=view)
            for i, (slug, (score, view)) in enumerate(ranked)
        ]

    def _exact_scores_for_slugs(
        self, slugs: list[str], vectors: tuple[list[float], list[float]]
    ) -> dict[str, tuple[float, str]]:
        """Точный (не ANN) CV-скор для КОНКРЕТНОГО множества slug'ов — Qdrant
        `scroll()` по payload-фильтру `slug in (...)` (MatchAny), максимум косинуса
        среди переданных query-векторов по КАЖДОЙ точке позиции (реальный + все
        synth-ракурсы). Векторы в коллекции и `vectors` оба уже L2-нормированы
        (`cv/encoder.py::encode()`) на Distance.COSINE — скалярное произведение
        численно равно тому же косинусу, что отдаёт `store.search()` (не отдельная,
        рассинхронизированная метрика). `scroll()`, не `query_points()` (ANN): для
        небольшого явного множества slug'ов (единицы-десятки, brief — "текст
        top-30") это ТОЧНО, не приближение через ANN-топ с фильтром поверх."""
        if not slugs or not self.store.client.collection_exists(self.collection):
            return {}
        flt = Filter(must=[FieldCondition(key="slug", match=MatchAny(any=slugs))])
        qvecs = [np.asarray(v, dtype=np.float32) for v in vectors]
        out: dict[str, tuple[float, str]] = {}
        offset = None
        while True:
            points, offset = self.store.client.scroll(
                self.collection, scroll_filter=flt, limit=512, offset=offset,
                with_vectors=True, with_payload=True,
            )
            for p in points:
                payload = p.payload or {}
                slug = payload.get("slug")
                if slug is None:
                    continue
                vec = np.asarray(p.vector, dtype=np.float32)
                score = max(float(np.dot(vec, qv)) for qv in qvecs)
                view = payload.get("view", "?")
                cur = out.get(slug)
                if cur is None or score > cur[0]:
                    out[slug] = (score, view)
            if offset is None:
                break
        return out

    def build(self, refs: dict[str, list[str]], version: str) -> None:
        """slug -> список путей [эталон, ...] (эталон + синтетические ракурсы, контракт).
        Порядок в списке — конвенция: первый элемент = "real". Остальные элементы
        различаются ПО ИМЕНИ ФАЙЛА, не только по позиции (G3-расширение для боевого
        индекса, agents/G3-real-index.md п.1: реальные позиции каталога кейса могут
        нести НЕСКОЛЬКО настоящих фото — ~30 слагов с `files` длиннее 1 в
        `case-data/slug_refs.json`): файл, отрендеренный `cv.augment.save_synthetic_
        views()` (имя вида `{slug}__synth-NN.*`), — "synth-N" по своему порядковому
        номеру среди синтетики этой позиции; ЛЮБОЙ другой файл — дополнительный
        РЕАЛЬНЫЙ ракурс той же позиции — "real" для первого, "real-2", "real-3"... для
        следующих по порядку в списке. Оба класса проходят ОДИНАКОВУЮ нормализацию —
        `prepare_reference()` буквально ЕСТЬ `normalize_query(enabled=True)` под другим
        именем (см. `cv/augment.py`, тот же результат численно) — так что классификация
        real/synth влияет ТОЛЬКО на строку `view` (какой ракурс сматчился, для отчётов/
        отладки), не на сами векторы (см. reports/g-report.md, "Предположения" — почему
        домен нормализации обязан быть одинаковым для real и synth).

        Полная переиндексация: коллекция пересоздаётся с нуля (в отличие от `add()`,
        который апсертит поверх). Тайминги по стадиям (load/normalize/embed/upsert,
        секунды) — в `self.last_build_stats` после вызова (не часть контракта).
        Прогресс — печать в stderr каждые `_BUILD_PROGRESS_EVERY` векторов (долгая
        сборка боевого индекса не должна идти молча, тот же пункт брифа)."""
        dim = self.encoder.dim
        self.store.recreate_collection(self.collection, dim)

        ids, vectors, payloads = [], [], []
        t_load = t_normalize = t_embed = 0.0
        t_start = time.perf_counter()
        for slug, paths in refs.items():
            real_i = 0
            synth_i = 0
            for path in paths:
                is_synth = "__synth-" in Path(path).name
                if is_synth:
                    synth_i += 1
                    view = f"synth-{synth_i}"
                else:
                    real_i += 1
                    view = "real" if real_i == 1 else f"real-{real_i}"

                t0 = time.perf_counter()
                arr = imageio.load_image_file(path)
                t1 = time.perf_counter()
                # ВАЖНО (см. reports/g-report.md, "Предположения" — нашёл на self-match):
                # и "real"(-N), и "synth"-N ракурсы прогоняются через ОДИНАКОВЫЙ
                # normalize_query() — тот же пайплайн, что search() применяет к запросу.
                # Ранняя версия применяла нормализацию только к "real" (через
                # prepare_reference), а "synth"-файлы (уже отрендеренные cv.augment.
                # save_synthetic_views) заносила в индекс СЫРЫМИ — из-за этого запрос
                # (после normalize_query) и его ближайшие соседи в индексе (synth-ракурсы
                # той же позиции, БЕЗ normalize_query) жили в разных "визуальных доменах",
                # и self-match проседал именно от этого рассинхрона.
                view_arr = prepare_reference(arr) if not is_synth else normalize_query(arr, enabled=True)
                t2 = time.perf_counter()
                vec = self.encoder.encode(view_arr)
                t3 = time.perf_counter()
                t_load += t1 - t0
                t_normalize += t2 - t1
                t_embed += t3 - t2

                ids.append(f"{slug}::{view}")
                vectors.append(vec)
                payloads.append({"slug": slug, "view": view})
                if len(ids) % _BUILD_PROGRESS_EVERY == 0:
                    elapsed = time.perf_counter() - t_start
                    rate = len(ids) / elapsed if elapsed > 0 else 0.0
                    print(
                        f"[ImageIndex.build] {len(ids)} векторов, {elapsed:.0f} с "
                        f"({rate:.1f} вект/с)",
                        file=sys.stderr,
                    )

        t_upsert0 = time.perf_counter()
        self.store.upsert(self.collection, ids, vectors, payloads)
        t_upsert = time.perf_counter() - t_upsert0

        self.last_build_stats = {
            "load_s": round(t_load, 2),
            "normalize_s": round(t_normalize, 2),
            "embed_s": round(t_embed, 2),
            "upsert_s": round(t_upsert, 2),
            "total_s": round(time.perf_counter() - t_start, 2),
        }

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
