"""Пути проекта. Любой путь переопределяется переменной окружения (см. README.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path(env: str, default: Path) -> Path:
    value = os.environ.get(env)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    catalog_csv: Path
    uploads_dir: Path
    artifacts_dir: Path
    photo_overrides_csv: Path


def get_paths() -> Paths:
    data_dir = _path("WINESCAN_DATA_DIR", PROJECT_ROOT / "data")
    return Paths(
        data_dir=data_dir,
        catalog_csv=_path("WINESCAN_CATALOG_CSV", data_dir / "strapi_output0709.csv"),
        uploads_dir=_path(
            "WINESCAN_UPLOADS_DIR",
            data_dir / "raw" / "prod-svoe-vino-strapi" / "prod-svoe-vino" / "strapi" / "uploads",
        ),
        artifacts_dir=_path("WINESCAN_ARTIFACTS_DIR", PROJECT_ROOT / "artifacts"),
        photo_overrides_csv=_path(
            "WINESCAN_PHOTO_OVERRIDES", PROJECT_ROOT / "configs" / "photo_overrides.csv"
        ),
    )
