"""Пути проекта. Любой путь переопределяется переменной окружения (см. README.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

def project_root(source_root: Path, cwd: Path) -> Path:
    """Корень пакета: ближайшая вверх папка с `pyproject.toml`.

    Раскладок две, и обе рабочие: отдельный репозиторий (`<корень>/src/winescan/`) и монорепо
    сервиса (`packages/winescan/winescan/`). Поиск вверх закрывает обе, не завися от имени
    промежуточной папки; глубина ограничена, чтобы не уехать в чужой проект уровнем выше.

    В Docker-образе пакет установлен в site-packages, и `pyproject.toml` рядом нет. Тогда корнем
    считается рабочая папка (в образе `/app`, куда скопированы `configs` и `Makefile`), иначе
    относительные пути вроде `configs/fusion_v2.json` не находятся."""
    for candidate in (source_root, *list(source_root.parents)[:2]):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cwd


PROJECT_ROOT = project_root(Path(__file__).resolve().parents[1], Path.cwd())


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
