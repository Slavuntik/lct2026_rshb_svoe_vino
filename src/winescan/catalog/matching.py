"""Привязка «Название фото» из CSV к оригиналу в uploads Strapi.

Дампа базы (таблиц files / files_related_morphs) нет, поэтому связь «вино -> файл»
восстанавливается по имени: ключ имени из CSV == ключ имени файла (см. strapi_names).
Если под ключом несколько разных файлов (типичные имена «Шардоне.webp», «2.webp»,
«Screenshot_16.webp» у разных виноделен), решения принимаются по порядку:

1. ``same_content`` — кандидаты одинаковы (sha256 или pHash ≤ порога): берём самый «студийный»,
   затем самый крупный;
2. ``exact_name`` — ровно у одного кандидата имя совпадает с «Название фото» с учётом границ
   слов («aligote-rkatsiteli» против «aligoterkatsiteli»);
3. ``upload_time`` — фото винодельни загружали пачками: берём кандидата, загруженного рядом
   по времени (≤ 2 ч) с уже однозначно привязанными фото той же винодельни, если следующий
   кандидат далеко (≥ 24 ч). Проверка на однозначных привязках: 97% фото лежат в пределах
   часа от другого фото своей винодельни, против 27% для случайной чужой винодельни;
4. ``winery_batch`` — у винодельни нет однозначных фото (например, у «Табии» все имена
   вида «Screenshot_N»): берём кандидата, рядом с которым по времени лежат кандидаты
   большинства других её вин;
5. иначе ``ambiguous`` — берём самый «студийный» кандидат и отправляем на ручную проверку.

Ручные решения из configs/photo_overrides.csv применяются последними со статусом ``manual``.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from winescan.catalog.images import packshot_score, phash_distance
from winescan.catalog.strapi_names import (
    is_format_variant,
    name_key,
    parse_upload,
    separator_key,
    split_extension,
)

HOUR = 3600.0


class Status:
    UNIQUE = "unique"
    SAME_CONTENT = "same_content"
    EXACT_NAME = "exact_name"
    UPLOAD_TIME = "upload_time"
    WINERY_BATCH = "winery_batch"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    MANUAL = "manual"


STATUS_DESCRIPTIONS = {
    Status.UNIQUE: "ровно один файл с таким именем",
    Status.SAME_CONTENT: "несколько файлов, но содержимое одинаковое",
    Status.EXACT_NAME: "разные файлы, выбран совпавший по имени с учётом границ слов",
    Status.UPLOAD_TIME: "выбран по времени загрузки рядом с другими фото винодельни",
    Status.WINERY_BATCH: "выбран по общей пачке загрузки с другими винами винодельни",
    Status.AMBIGUOUS: "разные файлы, автоматически не решено: выбрана догадка, нужна проверка",
    Status.MISSING: "файл не найден",
    Status.MANUAL: "решено вручную (configs/photo_overrides.csv)",
}


@dataclass(frozen=True)
class ResolveConfig:
    same_content_max_phash_distance: int = 6
    time_match_max_hours: float = 2.0
    time_runner_up_min_hours: float = 24.0
    batch_window_hours: float = 1.0
    batch_min_support: int = 2


def index_uploads(uploads_dir: Path) -> pd.DataFrame:
    if not uploads_dir.is_dir():
        raise FileNotFoundError(f"Нет папки uploads: {uploads_dir} (см. scripts/extract_dump.sh)")
    filenames = sorted(path.name for path in uploads_dir.iterdir() if path.is_file())
    all_names = frozenset(filenames)
    rows = []
    for filename in filenames:
        parsed = parse_upload(filename)
        stat = (uploads_dir / filename).stat()
        rows.append(
            {
                "filename": filename,
                "key": parsed.key,
                "file_hash": parsed.file_hash,
                "ext": parsed.ext,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "is_format_variant": is_format_variant(filename, all_names),
            }
        )
    return pd.DataFrame(rows)


def originals_by_key(uploads: pd.DataFrame) -> dict[str, list[str]]:
    originals = uploads[~uploads["is_format_variant"]]
    return originals.groupby("key")["filename"].apply(sorted).to_dict()


def find_candidates(photo_names: pd.Series, uploads: pd.DataFrame) -> pd.Series:
    by_key = originals_by_key(uploads)

    def lookup(photo_name: str) -> list[str]:
        stem, _ = split_extension(photo_name)
        # «x.png», пересохранённый в webp, Strapi хранит как «x_png_<hash>.webp»
        return by_key.get(name_key(stem)) or by_key.get(name_key(photo_name)) or []

    return photo_names.map(lookup)


def suggest_uploads(
    photo_name: str, by_key: Mapping[str, list[str]], limit: int = 3, cutoff: float = 0.75
) -> list[str]:
    """Похожие по ключу файлы для ненайденного фото: только подсказка для ручной проверки."""
    close = difflib.get_close_matches(name_key(split_extension(photo_name)[0]), list(by_key), limit, cutoff)
    return [filename for key in close for filename in by_key[key]]


def orphan_uploads_near(
    anchors: list[float], orphans: pd.DataFrame, max_hours: float = 6.0, limit: int = 12
) -> list[str]:
    """Непривязанные файлы, загруженные рядом по времени с фото винодельни (ближайшие первыми).

    Все 5 «missing» в выгрузке 0709 оказались такими: фото переименовали в медиатеке Strapi,
    поле name разошлось с именем файла, а сам файл остался «сиротой» из той же пачки загрузки.
    """
    if not anchors or orphans.empty:
        return []
    anchor_times = np.array(anchors, dtype=float)
    hours = orphans["mtime"].map(lambda m: float(np.abs(anchor_times - m).min()) / HOUR)
    near = orphans.assign(hours=hours)
    near = near[near["hours"] <= max_hours].sort_values("hours")
    return near["filename"].head(limit).tolist()


def _content_groups(files: list[str], images: Mapping[str, Mapping], max_distance: int) -> list[list[str]]:
    parent = list(range(len(files)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            a, b = images.get(files[i], {}), images.get(files[j], {})
            same_bytes = a.get("sha256") is not None and a.get("sha256") == b.get("sha256")
            close_hash = bool(a.get("phash") and b.get("phash")) and (
                phash_distance(a["phash"], b["phash"]) <= max_distance
            )
            if same_bytes or close_hash:
                parent[find(i)] = find(j)

    groups: dict[int, list[str]] = defaultdict(list)
    for i, filename in enumerate(files):
        groups[find(i)].append(filename)
    return list(groups.values())


def _area(info: Mapping) -> int:
    return (info.get("width") or 0) * (info.get("height") or 0)


def _representative(group: list[str], images: Mapping[str, Mapping], mtimes: Mapping[str, float]) -> str:
    """Лучший файл группы: студийный кадр (вырезанный фон) важнее разрешения."""
    return max(group, key=lambda f: (packshot_score(images.get(f, {})), _area(images.get(f, {})), mtimes[f]))


def _record(status: str, image_file: str | None, alternatives: list[str], note: str) -> dict:
    return {"image_status": status, "image_file": image_file, "image_alternatives": alternatives, "image_note": note}


def resolve_matches(
    catalog: pd.DataFrame,
    mtimes: Mapping[str, float],
    images: Mapping[str, Mapping],
    config: ResolveConfig = ResolveConfig(),
) -> pd.DataFrame:
    """catalog: колонки slug, winery, photo_name, candidates. Возвращает по строке на slug."""
    winery_of = dict(zip(catalog["slug"], catalog["winery"]))
    candidates_of = {slug: list(files) for slug, files in zip(catalog["slug"], catalog["candidates"])}
    records: dict[str, dict] = {}
    groups_of: dict[str, list[list[str]]] = {}

    def representatives(slug: str) -> list[str]:
        return [_representative(group, images, mtimes) for group in groups_of[slug]]

    def alternatives(slug: str, chosen: str) -> list[str]:
        return [_representative(g, images, mtimes) for g in groups_of[slug] if chosen not in g]

    for row in catalog.itertuples(index=False):
        slug, files = row.slug, candidates_of[row.slug]
        if not files:
            records[slug] = _record(Status.MISSING, None, [], "")
            continue
        groups = _content_groups(files, images, config.same_content_max_phash_distance)
        groups_of[slug] = groups
        if len(groups) == 1:
            status = Status.UNIQUE if len(files) == 1 else Status.SAME_CONTENT
            records[slug] = _record(status, _representative(groups[0], images, mtimes), [], "")
            continue
        photo_separator_key = separator_key(split_extension(row.photo_name)[0])
        exact = [g for g in groups if any(separator_key(parse_upload(f).base) == photo_separator_key for f in g)]
        if len(exact) == 1:
            chosen = _representative(exact[0], images, mtimes)
            note = "имя файла совпало с «Название фото» с учётом границ слов"
            records[slug] = _record(Status.EXACT_NAME, chosen, alternatives(slug, chosen), note)

    anchors: dict[str, list[float]] = defaultdict(list)
    for slug, record in records.items():
        if record["image_file"]:
            anchors[winery_of[slug]].append(mtimes[record["image_file"]])

    pending = [slug for slug in groups_of if slug not in records]
    for slug in pending:
        winery_anchors = anchors.get(winery_of[slug])
        if not winery_anchors:
            continue
        distances = sorted(
            (min(abs(mtimes[f] - anchor) for f in group for anchor in winery_anchors) / HOUR, index)
            for index, group in enumerate(groups_of[slug])
        )
        (best_hours, best_index), (runner_up_hours, _) = distances[0], distances[1]
        if best_hours <= config.time_match_max_hours and runner_up_hours >= config.time_runner_up_min_hours:
            chosen = _representative(groups_of[slug][best_index], images, mtimes)
            note = f"{best_hours:.1f} ч до фото винодельни, следующий кандидат {runner_up_hours:.0f} ч"
            records[slug] = _record(Status.UPLOAD_TIME, chosen, alternatives(slug, chosen), note)

    pending_by_winery: dict[str, list[str]] = defaultdict(list)
    for slug in pending:
        if slug not in records:
            pending_by_winery[winery_of[slug]].append(slug)

    window = config.batch_window_hours * HOUR
    for slugs in pending_by_winery.values():
        for slug in slugs:
            own = sorted(candidates_of[slug])
            others = [other for other in slugs if other != slug and sorted(candidates_of[other]) != own]
            support = sorted(
                (
                    sum(
                        any(abs(mtimes[f] - mtimes[c]) <= window for f in group for c in candidates_of[other])
                        for other in others
                    ),
                    index,
                )
                for index, group in enumerate(groups_of[slug])
            )[::-1]
            (best_support, best_index), runner_up_support = support[0], support[1][0]
            if best_support >= config.batch_min_support and best_support > runner_up_support:
                chosen = _representative(groups_of[slug][best_index], images, mtimes)
                note = f"в одной пачке загрузки с кандидатами {best_support} из {len(others)} других вин винодельни"
                records[slug] = _record(Status.WINERY_BATCH, chosen, alternatives(slug, chosen), note)

    for slug in pending:
        if slug not in records:
            chosen = max(
                representatives(slug),
                key=lambda f: (packshot_score(images.get(f, {})), _area(images.get(f, {})), mtimes[f]),
            )
            note = f"{len(groups_of[slug])} разных изображения, выбран самый «студийный»"
            records[slug] = _record(Status.AMBIGUOUS, chosen, alternatives(slug, chosen), note)

    return pd.DataFrame([{"slug": slug, **records[slug]} for slug in catalog["slug"]])


def load_overrides(path: Path) -> pd.DataFrame:
    columns = ["slug", "image_file", "reason"]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    overrides = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(columns) - set(overrides.columns)
    if missing:
        raise ValueError(f"{path}: нет колонок {sorted(missing)}")
    duplicated = overrides["slug"][overrides["slug"].duplicated()]
    if not duplicated.empty:
        raise ValueError(f"{path}: slug повторяется: {sorted(duplicated)}")
    return overrides[columns]


def apply_overrides(matches: pd.DataFrame, overrides: pd.DataFrame, known_files: set[str]) -> pd.DataFrame:
    """Пустой image_file в override означает «у вина нет подходящего эталона»."""
    unknown_slugs = set(overrides["slug"]) - set(matches["slug"])
    unknown_files = {f for f in overrides["image_file"] if f} - known_files
    if unknown_slugs or unknown_files:
        raise ValueError(f"Overrides: неизвестные slug {sorted(unknown_slugs)}, файлы {sorted(unknown_files)}")

    matches = matches.copy()
    for row in overrides.itertuples(index=False):
        mask = matches["slug"] == row.slug
        matches.loc[mask, "image_status"] = Status.MANUAL
        matches.loc[mask, "image_file"] = row.image_file or None
        matches.loc[mask, "image_note"] = row.reason
    return matches
