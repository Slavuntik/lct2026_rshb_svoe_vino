"""Чтение и очистка CSV-выгрузки каталога Strapi (strapi_output0709.csv)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

COLUMN_MAP = {
    "Название вина": "name",
    "Категория": "category",
    "Цвет": "color",
    "Регион": "region",
    "Сорт винограда": "grapes",
    "Описание": "description",
    "Винодельня": "winery",
    "Slug": "slug",
    "Название фото": "photo_name",
}


@dataclass
class LoadStats:
    rows_raw: int = 0
    rows_after_exact_dedup: int = 0
    rows_after_strip_dedup: int = 0
    unique_slugs: int = 0
    conflicting_slugs: list[str] = field(default_factory=list)
    empty_values: dict[str, int] = field(default_factory=dict)


def load_catalog_csv(path: Path) -> tuple[pd.DataFrame, LoadStats]:
    """Читает CSV, убирает дубли строк и пробелы по краям; одна строка на slug.

    В выгрузке 0709 почти каждая строка повторена дважды. Если под одним slug
    окажутся разные данные, берём первую строку, а slug попадает в ``conflicting_slugs``.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(COLUMN_MAP) - set(raw.columns)
    if missing:
        raise ValueError(f"В CSV нет колонок: {sorted(missing)}")

    stats = LoadStats(rows_raw=len(raw))
    stats.rows_after_exact_dedup = len(raw.drop_duplicates())

    df = raw[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    df = df.apply(lambda column: column.str.strip())
    df = df.drop_duplicates()
    stats.rows_after_strip_dedup = len(df)

    duplicated = df["slug"].duplicated(keep=False)
    stats.conflicting_slugs = sorted(df.loc[duplicated, "slug"].unique())
    df = df.drop_duplicates(subset="slug", keep="first").reset_index(drop=True)
    stats.unique_slugs = len(df)
    stats.empty_values = {col: int((df[col] == "").sum()) for col in df.columns if (df[col] == "").any()}

    df["grapes_list"] = df["grapes"].map(
        lambda value: [grape.strip() for grape in value.split(",") if grape.strip()]
    )
    return df, stats
