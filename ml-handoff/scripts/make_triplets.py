#!/usr/bin/env python3
"""ml-handoff/scripts/make_triplets.py — генератор манифеста триплетов для metric
fine-tune (см. ml-handoff/RECIPE.md, по мотивам arXiv:2404.08820).

Источники данных — ТОЛЬКО то, что уже существует и проверено (ничего не считает
заново, ничего не обучает):
  - `case-data/slug_refs.json` (F3, `qa/case_census.py`, reports/f3-case-census.md,
    commit 95b8891) — slug -> эталонное фото + доп. кандидаты + usable-флаг.
  - `case-data/families.json` (F3, тот же коммит) — 165 near-dup семей / 364 слага,
    "готовый майнинг" hard negatives по брифу agents/M1-ml-handoff.md.
  - `packages/cv/cv/augment.py::render_synthetic_views` — ТОЛЬКО ссылка на функцию и
    её параметры (seed, view_index), картинки НЕ рендерятся и НЕ сохраняются здесь.

Манифест — JSONL, один объект на "якорь" (usable-слаг): anchor (путь к эталону),
positives (спецификации позитивов — синтетические ракурсы ТОГО ЖЕ эталона +, если
есть, альтернативные реальные фото того же слага) и negatives (чужие члены той же
near-dup семьи + случайные "прочие" слаги, опционально — ближайшие соседи по
эмбеддингу). guardrail брифа: "БЕЗ копирования фото — только пути" — в манифесте нет
ни одного байта изображения, только абсолютные пути в `case-data/` (вне git) и
детерминированные рецепты рендера.

Почему positives — рецепт, а не готовые файлы: `render_synthetic_views(image, n, seed)`
полностью детерминирована (один `numpy.random.Generator(seed)`, параметры тянутся
строго по порядку) и ПРЕФИКС-СОГЛАСОВАНА по n: `render_synthetic_views(img, n=K, seed=S)[i]`
даёt побайтово тот же результат для ЛЮБОГО K > i (проверено эмпирически в
ml-handoff/tests/test_make_triplets.py::test_view_recipe_is_prefix_consistent_with_augment) —
поэтому "seed + view_index" достаточно, чтобы ML-команда воспроизвела ТОЧНО ту же
картинку из самого эталона, ничего не копируя и не сериализуя.

ЗАПУСК: строго venv `packages/cv` (нужны cv2/numpy/torch/transformers из него, а сам
модуль `cv` установлен туда editable-пакетом):

    cd svoy-somelye
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 packages/cv/.venv/bin/python \\
        ml-handoff/scripts/make_triplets.py --out /path/to/triplets.jsonl

По умолчанию скрипт НИКОГДА не открывает `cv.index.ImageIndex`/Qdrant (никакого
импорта `cv.index`/`cv.store`) — это намеренно: на машине может быть живой стенд
(порт :8000), который держит файловый лок эмбеддед-Qdrant в `packages/cv/data/qdrant/`,
и мы обязаны его не трогать (agents/M1-ml-handoff.md, "Не делать"). Опциональные
"соседи из индекса" (`--neighbor-negatives`) считаются ТОЛЬКО через голый энкодер
(`cv.encoder.SiglipEncoder.encode`, чистый inference, без стора) — см. функцию
`build_embeddings_cache` ниже; сам поиск по кэшу — обычная numpy-арифметика.

Коды возврата (та же конвенция, что `qa/scan_eval.py`): 0 — манифест записан;
2 — ошибка использования (не найден slug_refs.json/families.json, пустой каталог и т.п.).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------------------
# Сожжённые сиды синтетики проекта (ml-handoff/DATA.md, таблица "Сожжённые сиды"):
#   0        — AUGMENT_SEED_DEFAULT, синтетические ракурсы ВНУТРИ индекса (cv/config.py)
#   9973     — HOLDOUT_SEED_OFFSET, self-match дельта-проверки G3 (cv/selfcheck.py)
#   20260917 — F3 полномасштабный zero-shot baseline + финальный гейт-eval (B6/B7)
#   314159   — B4/B5 приёмочная выборка 150 фото
# Ни один из них НЕ переиспользуется здесь: если позитивы для обучения будут
# побайтово совпадать с картинками уже опубликованного eval-прогона, дальнейший eval
# на том же seed перестаёт быть честным (см. RECIPE.md, "риск переобучения").
# --------------------------------------------------------------------------------------
DEFAULT_TRIPLET_SEED = 555001

DEFAULT_POSITIVES_PER_ANCHOR = 4
DEFAULT_NEG_FAMILY_CAP = 7  # максимальный размер семьи в кейсе - 1 = 8-1 (f3-case-census.md §2.2)
DEFAULT_NEG_RANDOM_CAP = 4
MULTI_CANDIDATE_CAUTION_THRESHOLD = 4  # f3-case-census.md §1.2: "12 из них — с ≥4 кандидатами"

DEFAULT_UPLOADS_SUBPATH = "prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"


# --------------------------------------------------------------------------------------
# Записи манифеста
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PositiveSpec:
    kind: str  # "synthetic_view" | "real_alternate"
    path: str | None = None  # real_alternate
    caution: bool | None = None  # real_alternate — см. MULTI_CANDIDATE_CAUTION_THRESHOLD
    seed: int | None = None  # synthetic_view
    view_index: int | None = None
    n_views: int | None = None
    generator: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None} | {"kind": self.kind}


@dataclass(frozen=True)
class NegativeSpec:
    kind: str  # "family_hard" | "random" | "index_neighbor"
    slug: str
    path: str
    family_id: str | None = None
    similarity: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None} | {"kind": self.kind, "slug": self.slug, "path": self.path}


@dataclass(frozen=True)
class TripletGroup:
    """Один "якорь" (usable-слаг) + его позитивы/негативы. Не плоский триплет —
    сознательно: рецепт статьи (arXiv:2404.08820) — batch-all triplet metric learning,
    т.е. лосс сам перебирает валидные комбинации (anchor, positive, negative) внутри
    батча; хранить их заранее развёрнутыми — раздувать манифест ×|positives|×|negatives|
    без пользы (см. RECIPE.md)."""

    slug: str
    winery: str
    name: str
    family_id: str | None
    ref_quality_class: str | None
    anchor_path: str
    positives: list[PositiveSpec]
    negatives: list[NegativeSpec]

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "winery": self.winery,
            "name": self.name,
            "family_id": self.family_id,
            "ref_quality_class": self.ref_quality_class,
            "anchor": {"path": self.anchor_path, "kind": "reference"},
            "positives": [p.to_json() for p in self.positives],
            "negatives": [n.to_json() for n in self.negatives],
        }


# --------------------------------------------------------------------------------------
# Загрузка каталога кейса — переиспользуем cv.cli/cv.families (не переизобретаем
# схему chosen/candidates/usable и обёртки families.json — за них уже отвечают тесты
# packages/cv, см. cv/tests/test_cli.py, test_families.py)
# --------------------------------------------------------------------------------------


def _import_cv():
    """Отложенный импорт с внятной ошибкой, если запущено не тем python (бриф:
    "гоняй venv'ом packages/cv"). НЕ импортирует cv.index/cv.store (см. докстринг
    модуля) — только cli (пути) и families (near-dup членство)."""
    try:
        from cv.cli import discover_refs_from_slug_refs_json, load_near_dup_groups
        from cv.families import load_family_by_slug
    except ImportError as exc:  # pragma: no cover — путь ошибки, не нужен в CI
        raise SystemExit(
            "make_triplets.py требует пакет `cv` (packages/cv) — запускай через "
            "packages/cv/.venv/bin/python, не системным python.\n"
            f"Исходная ошибка импорта: {exc}"
        ) from exc
    return discover_refs_from_slug_refs_json, load_near_dup_groups, load_family_by_slug


@dataclass(frozen=True)
class Anchor:
    slug: str
    winery: str
    name: str
    ref_quality_class: str | None
    path: Path
    extra_real: list[Path]
    multi_candidate_count: int


def load_anchors(case_data_dir: Path, uploads_dir: Path) -> dict[str, Anchor]:
    """usable-слаги -> Anchor. Путь эталона/доп.ракурсов — через
    `cv.cli.discover_refs_from_slug_refs_json` (та же функция, что использует G3 при
    сборке боевого индекса, `reports/g3-real-index.md`) — гарантирует, что "usable"
    и порядок chosen/candidates не разъедутся с боевой сборкой индекса."""
    discover_refs_from_slug_refs_json, _, _ = _import_cv()
    refs_json = case_data_dir / "slug_refs.json"
    if not refs_json.is_file():
        raise FileNotFoundError(f"не найден {refs_json} (case-data — читать можно, но локально, вне git)")
    payload = json.loads(refs_json.read_text(encoding="utf-8"))
    mapping = payload.get("mapping", payload)

    refs_by_slug = discover_refs_from_slug_refs_json(refs_json, uploads_dir)  # slug -> [эталон, *доп.]

    out: dict[str, Anchor] = {}
    for slug, paths in refs_by_slug.items():
        info = mapping.get(slug, {})
        candidates = info.get("candidates") or []
        out[slug] = Anchor(
            slug=slug,
            winery=info.get("winery", ""),
            name=info.get("name", ""),
            ref_quality_class=info.get("ref_quality_class"),
            path=paths[0],
            extra_real=list(paths[1:]),
            multi_candidate_count=len(candidates),
        )
    return out


def load_family_index(families_json: Path) -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """(slug -> family_id, slug -> члены его семьи включая себя). Пустой families.json
    (или его отсутствие) -> пустые словари, не ошибка — тот же контракт, что
    `cv.families.load_family_by_slug`/`cv.cli.load_near_dup_groups` (families.py:
    "отсутствие... не как ошибка")."""
    _, load_near_dup_groups, load_family_by_slug = _import_cv()
    family_by_slug = load_family_by_slug(families_json)
    groups = load_near_dup_groups(families_json)
    members_by_slug: dict[str, frozenset[str]] = {}
    for group in groups:
        for s in group:
            members_by_slug[s] = group
    return family_by_slug, members_by_slug


# --------------------------------------------------------------------------------------
# Позитивы
# --------------------------------------------------------------------------------------


def build_positive_specs(anchor: Anchor, n_positives: int, n_views: int, seed: int) -> list[PositiveSpec]:
    """Позитивы = синтетические ракурсы ТОГО ЖЕ эталона (cv/augment.py, RECIPE.md) +,
    если у слага реально есть альтернативные РЕАЛЬНЫЕ фото (slug_refs.json.candidates
    за вычетом chosen — не у всех слагов, см. DATA.md), они добавляются отдельным
    типом `real_alternate`. Множественные кандидаты — не всегда одно и то же фото
    (f3-case-census.md §1.2: "12 слагов с ≥4 кандидатами... выбор технически
    детерминирован, но по сути произволен между визуально РАЗНЫМИ фото") — такие
    помечены `caution=true`, ML-команде стоит просмотреть глазами перед использованием."""
    n_positives = max(0, min(n_positives, n_views))
    positives = [
        PositiveSpec(
            kind="synthetic_view",
            seed=seed,
            view_index=i,
            n_views=n_views,
            generator="cv.augment.render_synthetic_views",
        )
        for i in range(n_positives)
    ]
    for extra_path in anchor.extra_real:
        positives.append(
            PositiveSpec(
                kind="real_alternate",
                path=str(extra_path),
                caution=anchor.multi_candidate_count >= MULTI_CANDIDATE_CAUTION_THRESHOLD,
            )
        )
    return positives


# --------------------------------------------------------------------------------------
# Негативы
# --------------------------------------------------------------------------------------


def build_family_negatives(
    anchor_slug: str,
    family_id: str | None,
    family_members: frozenset[str],
    anchors: dict[str, Anchor],
    cap: int,
) -> list[NegativeSpec]:
    """Чужие члены той же near-dup семьи (families.json — "готовый майнинг" по
    брифу) — самые информативные hard negatives: одна линейка, отличаются
    год/сезон/категория при похожей/одинаковой этикетке (case.md), визуально почти
    неотличимы для zero-shot CV (reports/g3-real-index.md §5: медиана разрыва
    0.023 < CV_GROUP_EPSILON=0.03 на семьях с разными фото)."""
    others = sorted(s for s in family_members if s != anchor_slug and s in anchors)
    out = []
    for s in others[:cap]:
        out.append(NegativeSpec(kind="family_hard", slug=s, path=str(anchors[s].path), family_id=family_id))
    return out


def build_random_negatives(
    anchor_slug: str,
    family_members: frozenset[str],
    anchors: dict[str, Anchor],
    all_slugs_sorted: list[str],
    cap: int,
    seed: int,
) -> list[NegativeSpec]:
    """Случайные "прочие" негативы — НАШЕ дополнение сверх брифа (семья + соседи из
    индекса), обосновано числами DATA.md: 1647/1982 (83.1%) usable-слагов НЕ входят
    ни в одну near-dup семью (families.json покрывает только 335/1982) — без этого
    источника у подавляющего большинства якорей не было бы негативов вовсе.
    Детерминировано per-slug (`random.Random(f"{seed}:{slug}")`), не общим
    перемешиванием — так добавление новых слагов в каталог не меняет уже выданные
    негативы соседей (тот же принцип устойчивости, что `scan_eval.py::assign_split`)."""
    pool = [s for s in all_slugs_sorted if s != anchor_slug and s not in family_members]
    if not pool or cap <= 0:
        return []
    rng = random.Random(f"{seed}:{anchor_slug}")
    chosen = rng.sample(pool, k=min(cap, len(pool)))
    return [NegativeSpec(kind="random", slug=s, path=str(anchors[s].path)) for s in sorted(chosen)]


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def build_neighbor_negatives(
    anchor_slug: str,
    embeddings: dict[str, list[float]],
    exclude: set[str],
    cap: int,
    anchors: dict[str, Anchor],
) -> list[NegativeSpec]:
    """Ближайшие соседи ПО ЭМБЕДДИНГУ (не по семье) — второй источник hard negatives
    из брифа. Строго опционально: нужен `--embeddings-cache` (см. `build_embeddings_cache`
    ниже) — НЕ считается по умолчанию, чтобы обычный прогон манифеста оставался
    быстрым (секунды, без модели) и не зависел от тёплого HF-кэша."""
    query = embeddings.get(anchor_slug)
    if query is None or cap <= 0:
        return []
    scored = [
        (slug, _cosine(query, vec))
        for slug, vec in embeddings.items()
        if slug != anchor_slug and slug not in exclude and slug in anchors
    ]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [
        NegativeSpec(kind="index_neighbor", slug=s, path=str(anchors[s].path), similarity=round(sim, 6))
        for s, sim in scored[:cap]
    ]


# --------------------------------------------------------------------------------------
# Опциональный офлайн эмбеддинг-кэш (энкодер напрямую, БЕЗ Qdrant/стора — см. докстринг
# модуля про безопасность стенда :8000)
# --------------------------------------------------------------------------------------


def build_embeddings_cache(anchors: dict[str, Anchor], out_path: Path, limit: int | None = None) -> dict[str, list[float]]:
    """Кодирует эталон КАЖДОГО якоря `cv.encoder.SiglipEncoder.encode()` напрямую —
    ни `cv.index.ImageIndex`, ни `cv.store.QdrantStore` здесь не импортируются и не
    создаются, так что файловый лок `packages/cv/data/qdrant/.lock` (может
    удерживаться живым стендом) не трогается вообще. Дорогая операция (энкодинг на
    CPU/MPS, минуты на полный каталог, embed p95 ~100 мс/фото на боевых фото —
    `reports/g3-real-index.md` §3) — отдельный шаг, не часть обычного прогона
    манифеста."""
    from cv.encoder import SiglipEncoder
    from cv.imageio import load_image_file

    encoder = SiglipEncoder()
    items = sorted(anchors.items())
    if limit is not None:
        items = items[:limit]
    cache: dict[str, list[float]] = {}
    for slug, anchor in items:
        image = load_image_file(str(anchor.path))
        cache[slug] = encoder.encode(image)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cache), encoding="utf-8")
    return cache


# --------------------------------------------------------------------------------------
# Сборка манифеста
# --------------------------------------------------------------------------------------


@dataclass
class Summary:
    anchors_total: int = 0
    skipped_unusable_or_missing: int = 0
    anchors_with_family_negatives: int = 0
    distinct_families_used: int = 0
    positives_total: int = 0
    negatives_total: int = 0
    negatives_family_total: int = 0
    negatives_random_total: int = 0
    negatives_neighbor_total: int = 0
    triplets_expanded_total: int = 0  # sum(len(positives) * len(negatives)) по якорям

    def to_json(self) -> dict[str, Any]:
        return dict(self.__dict__)


def build_manifest(
    case_data_dir: Path,
    uploads_dir: Path,
    *,
    positives_per_anchor: int,
    n_views: int,
    seed: int,
    neg_family_cap: int,
    neg_random_cap: int,
    neg_neighbor_cap: int,
    embeddings: dict[str, list[float]] | None,
    limit: int | None,
) -> tuple[list[TripletGroup], Summary]:
    anchors = load_anchors(case_data_dir, uploads_dir)
    family_by_slug, members_by_slug = load_family_index(case_data_dir / "families.json")

    all_slugs_sorted = sorted(anchors)
    if limit is not None:
        all_slugs_sorted = all_slugs_sorted[:limit]

    groups: list[TripletGroup] = []
    summary = Summary()
    families_seen: set[str] = set()

    for slug in all_slugs_sorted:
        anchor = anchors[slug]
        summary.anchors_total += 1

        positives = build_positive_specs(anchor, positives_per_anchor, n_views, seed)

        family_id = family_by_slug.get(slug)
        family_members = members_by_slug.get(slug, frozenset())
        family_negs = build_family_negatives(slug, family_id, family_members, anchors, neg_family_cap)
        random_negs = build_random_negatives(slug, family_members, anchors, sorted(anchors), neg_random_cap, seed)
        neighbor_negs: list[NegativeSpec] = []
        if embeddings and neg_neighbor_cap > 0:
            exclude = {slug} | family_members | {n.slug for n in random_negs}
            neighbor_negs = build_neighbor_negatives(slug, embeddings, exclude, neg_neighbor_cap, anchors)

        negatives = family_negs + random_negs + neighbor_negs
        if not negatives:
            # Без негативов триплет не триплет — по построению не должно случаться
            # (random-негативы покрывают даже слаги без семьи, см. build_random_negatives),
            # но проверяем честно, а не молчим.
            continue

        if family_negs:
            summary.anchors_with_family_negatives += 1
            if family_id:
                families_seen.add(family_id)

        summary.positives_total += len(positives)
        summary.negatives_total += len(negatives)
        summary.negatives_family_total += len(family_negs)
        summary.negatives_random_total += len(random_negs)
        summary.negatives_neighbor_total += len(neighbor_negs)
        summary.triplets_expanded_total += len(positives) * len(negatives)

        groups.append(
            TripletGroup(
                slug=slug,
                winery=anchor.winery,
                name=anchor.name,
                family_id=family_id,
                ref_quality_class=anchor.ref_quality_class,
                anchor_path=str(anchor.path),
                positives=positives,
                negatives=negatives,
            )
        )

    summary.distinct_families_used = len(families_seen)
    return groups, summary


def write_manifest(groups: Iterable[TripletGroup], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for g in groups:
            fh.write(json.dumps(g.to_json(), ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _default_case_data_dir() -> Path:
    try:
        from cv import config

        return config.CASE_DATA_DIR
    except ImportError:
        import os

        return Path(os.environ.get("CASE_DATA_DIR", "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"))


def _default_n_views() -> int:
    try:
        from cv.augment import VIEWS_DEFAULT

        return VIEWS_DEFAULT
    except ImportError:
        return 24


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--case-data-dir", type=Path, default=None, help="дефолт: cv.config.CASE_DATA_DIR / $CASE_DATA_DIR")
    p.add_argument("--uploads-dir", type=Path, default=None, help=f"дефолт: <case-data-dir>/{DEFAULT_UPLOADS_SUBPATH}")
    p.add_argument("--out", type=Path, default=None, help="путь JSONL-манифеста (обязателен, кроме --build-embeddings-cache)")
    p.add_argument("--positives-per-anchor", type=int, default=DEFAULT_POSITIVES_PER_ANCHOR)
    p.add_argument("--n-views", type=int, default=None, help="дефолт: cv.augment.VIEWS_DEFAULT (24)")
    p.add_argument("--seed", type=int, default=DEFAULT_TRIPLET_SEED, help="НЕ переиспользуй сожжённые сиды — см. DATA.md")
    p.add_argument("--neg-family-cap", type=int, default=DEFAULT_NEG_FAMILY_CAP)
    p.add_argument("--neg-random-cap", type=int, default=DEFAULT_NEG_RANDOM_CAP)
    p.add_argument("--neighbor-negatives", type=int, default=0, dest="neg_neighbor_cap", help="0 = выключено (дефолт)")
    p.add_argument("--embeddings-cache", type=Path, default=None, help="JSON {slug: [float,...]} — см. --build-embeddings-cache")
    p.add_argument("--build-embeddings-cache", type=Path, default=None, help="только построить кэш эмбеддингов якорей и выйти")
    p.add_argument("--limit", type=int, default=None, help="ограничить число якорей (смоук-прогоны)")
    p.add_argument("--summary-out", type=Path, default=None, help="дефолт: <out>.summary.json")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    case_data_dir = args.case_data_dir or _default_case_data_dir()
    uploads_dir = args.uploads_dir or (case_data_dir / DEFAULT_UPLOADS_SUBPATH)

    if not case_data_dir.is_dir():
        print(f"[make_triplets] каталог кейса не найден: {case_data_dir}", file=sys.stderr)
        return 2
    if not uploads_dir.is_dir():
        print(f"[make_triplets] каталог uploads не найден: {uploads_dir}", file=sys.stderr)
        return 2

    if args.build_embeddings_cache is not None:
        anchors = load_anchors(case_data_dir, uploads_dir)
        cache = build_embeddings_cache(anchors, args.build_embeddings_cache, limit=args.limit)
        print(f"[make_triplets] embeddings-cache: {len(cache)} якорей -> {args.build_embeddings_cache}")
        return 0

    if args.out is None:
        print("[make_triplets] нужен --out (или --build-embeddings-cache)", file=sys.stderr)
        return 2

    embeddings = None
    if args.embeddings_cache is not None:
        if not args.embeddings_cache.is_file():
            print(f"[make_triplets] embeddings-cache не найден: {args.embeddings_cache}", file=sys.stderr)
            return 2
        embeddings = json.loads(args.embeddings_cache.read_text(encoding="utf-8"))

    try:
        groups, summary = build_manifest(
            case_data_dir,
            uploads_dir,
            positives_per_anchor=args.positives_per_anchor,
            n_views=args.n_views or _default_n_views(),
            seed=args.seed,
            neg_family_cap=args.neg_family_cap,
            neg_random_cap=args.neg_random_cap,
            neg_neighbor_cap=args.neg_neighbor_cap,
            embeddings=embeddings,
            limit=args.limit,
        )
    except FileNotFoundError as exc:
        print(f"[make_triplets] ОШИБКА: {exc}", file=sys.stderr)
        return 2

    if not groups:
        print("[make_triplets] ОШИБКА: пустой манифест (0 usable-якорей после фильтров)", file=sys.stderr)
        return 2

    write_manifest(groups, args.out)
    summary_out = args.summary_out or args.out.with_suffix(args.out.suffix + ".summary.json")
    summary_out.write_text(json.dumps(summary.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"[make_triplets] {summary.anchors_total} якорей "
        f"({summary.anchors_with_family_negatives} с hard-негативом из семьи, "
        f"{summary.distinct_families_used} различных семей) -> {args.out}\n"
        f"[make_triplets] позитивов {summary.positives_total}, негативов {summary.negatives_total} "
        f"(family={summary.negatives_family_total} random={summary.negatives_random_total} "
        f"neighbor={summary.negatives_neighbor_total})\n"
        f"[make_triplets] развёрнутых (anchor,pos,neg) триплетов: {summary.triplets_expanded_total}\n"
        f"[make_triplets] сводка -> {summary_out}"
    )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
