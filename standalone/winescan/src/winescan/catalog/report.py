"""Markdown-отчёт сборки каталога (artifacts/catalog/build_report.md)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from winescan.catalog.loader import LoadStats
from winescan.catalog.matching import STATUS_DESCRIPTIONS, Status


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |" for row in rows]
    return "\n".join(lines)


def _percentiles(values: pd.Series) -> str:
    values = values.dropna()
    if values.empty:
        return "—"
    p = np.percentile(values, [0, 5, 50, 95, 100])
    return " / ".join(f"{v:.0f}" if v >= 10 else f"{v:.2f}" for v in p)


def render_report(
    *,
    paths: dict[str, Path],
    load_stats: LoadStats,
    uploads: pd.DataFrame,
    catalog: pd.DataFrame,
    candidate_images: pd.DataFrame,
    near_duplicates: pd.DataFrame,
    min_distance_counts: dict[str, int],
    near_duplicate_threshold: int,
    review_sheets: dict[str, list[Path]],
    elapsed_seconds: float,
) -> str:
    total = len(catalog)
    out: list[str] = [
        "# Отчёт сборки каталога",
        "",
        f"Сгенерировано `python -m winescan.catalog.build` {datetime.now():%Y-%m-%d %H:%M}, "
        f"время сборки {elapsed_seconds:.0f} с. Файл перезаписывается при каждой сборке.",
        "",
        "## Источники",
        "",
        _table(["что", "путь"], [[name, f"`{path}`"] for name, path in paths.items()]),
        "",
        "## CSV-выгрузка",
        "",
        _table(["показатель", "значение"], [[k, v] for k, v in asdict(load_stats).items()]),
        "",
        "## Папка uploads",
        "",
        _table(
            ["показатель", "значение"],
            [
                ["файлов всего", len(uploads)],
                ["оригиналов", int((~uploads["is_format_variant"]).sum())],
                ["уменьшенных копий Strapi (thumbnail/small/medium/large)", int(uploads["is_format_variant"].sum())],
            ],
        ),
        "",
        "## Привязка эталонных фото",
        "",
    ]

    counts = catalog["image_status"].value_counts()
    order = [Status.UNIQUE, Status.SAME_CONTENT, Status.EXACT_NAME, Status.UPLOAD_TIME, Status.WINERY_BATCH,
             Status.MANUAL, Status.AMBIGUOUS, Status.MISSING]  # fmt: skip
    out += [
        _table(
            ["статус", "вин", "доля", "смысл"],
            [[f"`{s}`", int(counts.get(s, 0)), f"{counts.get(s, 0) / total:.1%}", STATUS_DESCRIPTIONS[s]] for s in order],
        ),
        "",
        f"С эталонным изображением: **{catalog['image_file'].notna().sum()} из {total}**. "
        f"Разных файлов-эталонов: {catalog['image_file'].nunique()}.",
        "",
        "## Атрибуты из названия и slug",
        "",
        _table(
            ["атрибут", "заполнено"],
            [
                ["год", f"{catalog['year'].notna().mean():.1%}"],
                ["сладость", f"{catalog['sweetness'].notna().mean():.1%}"],
                ["игристое (признак найден)", f"{catalog['sparkling'].mean():.1%}"],
                ["объём", f"{catalog['volume_l'].notna().mean():.1%}"],
            ],
        ),
        "",
        "Сладость: "
        + ", ".join(f"{k} {v}" for k, v in catalog["sweetness"].fillna("не найдено").value_counts().items()),
        "",
        "## Эталонные изображения (выбранные)",
        "",
    ]

    chosen = catalog[catalog["image_file"].notna()]
    aspect = chosen["image_height"] / chosen["image_width"]
    max_side = chosen[["image_width", "image_height"]].max(axis=1)
    errors = candidate_images[candidate_images["error"].notna()]
    out += [
        _table(
            ["показатель", "min / p5 / медиана / p95 / max"],
            [
                ["ширина, px", _percentiles(chosen["image_width"])],
                ["высота, px", _percentiles(chosen["image_height"])],
                ["большая сторона, px", _percentiles(max_side)],
                ["высота / ширина", _percentiles(aspect)],
            ],
        ),
        "",
        f"С прозрачным фоном (> 5% прозрачных пикселей): {(chosen['image_transparent_share'] > 0.05).mean():.1%}. "
        f"Форматы: {', '.join(f'{k} {v}' for k, v in chosen['image_format'].value_counts().items())}. "
        f"Не открылись среди кандидатов: {len(errors)}.",
        "",
        "## Визуальные дубли среди эталонов (pHash)",
        "",
        f"Группы с расстоянием pHash ≤ {near_duplicate_threshold} из 64 бит: "
        f"**{near_duplicates['group_id'].nunique() if not near_duplicates.empty else 0}** групп, "
        f"в них {len(near_duplicates)} вин.",
        "",
        "Расстояние от эталона до ближайшего чужого эталона:",
        "",
        _table(["расстояние pHash", "вин"], [[k, v] for k, v in min_distance_counts.items()]),
        "",
        "pHash считается по яркости на низких частотах: он видит силуэт бутылки и вёрстку этикетки,",
        "но не цвет и не мелкий текст. Поэтому в одну группу попадают вина одной серии",
        "(«100 оттенков», Adagum), которые отличаются только сортом или годом на этикетке, а иногда",
        "и разные по цвету этикетки (Abrau Estates белая и оранжевая). Это оценка того, сколько вин",
        "нельзя надёжно различить по глобальному виду эталона: для них нужен текст этикетки (OCR).",
        "",
        "## Требуют внимания",
        "",
    ]

    missing = catalog[catalog["image_status"] == Status.MISSING]
    out += [
        f"### Без эталона ({len(missing)})",
        "",
        _table(
            ["slug", "винодельня", "Название фото", "похожие файлы"],
            [[r.slug, r.winery, r.photo_name, ", ".join(r.suggestions) or "—"] for r in missing.itertuples()],
        )
        if len(missing)
        else "Нет.",
        "",
    ]
    ambiguous = catalog[catalog["image_status"] == Status.AMBIGUOUS]
    out += [
        f"### Неоднозначные, не проверены вручную ({len(ambiguous)})",
        "",
        _table(
            ["slug", "винодельня", "выбран", "альтернативы"],
            [[r.slug, r.winery, r.image_file, ", ".join(r.image_alternatives)] for r in ambiguous.itertuples()],
        )
        if len(ambiguous)
        else "Нет.",
        "",
        "## Листы ручной проверки",
        "",
    ]
    for name, sheet_paths in review_sheets.items():
        out.append(f"- {name}: {len(sheet_paths)} шт. ({', '.join(p.name for p in sheet_paths) or '—'})")
    out.append("")
    return "\n".join(out)
