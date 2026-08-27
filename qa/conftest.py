"""Общие пути для всего qa/: реальные данные vines (read-only) и синтетические фикстуры."""

from __future__ import annotations

from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parent
REPO_ROOT = QA_DIR.parent  # svoy-somelye/
VINES_ROOT = REPO_ROOT.parent  # vines/ — данные read-only, см. ORCHESTRATION.md

REAL_CATALOG_DIR = VINES_ROOT / "catalog"
REAL_REF_DIR = VINES_ROOT / "ref"

MINI_CATALOG_DIR = QA_DIR / "tests" / "fixtures" / "mini_catalog"
MINI_REF_DIR = QA_DIR / "tests" / "fixtures" / "mini_ref"


@pytest.fixture(scope="session")
def real_catalog_dir() -> Path:
    assert REAL_CATALOG_DIR.is_dir(), f"vines/catalog не найден по {REAL_CATALOG_DIR} — проверь расположение репозитория"
    return REAL_CATALOG_DIR


@pytest.fixture(scope="session")
def real_ref_dir() -> Path:
    assert REAL_REF_DIR.is_dir(), f"vines/ref не найден по {REAL_REF_DIR}"
    return REAL_REF_DIR


@pytest.fixture(scope="session")
def mini_catalog_dir() -> Path:
    assert MINI_CATALOG_DIR.is_dir(), f"синтетическая фикстура не найдена: {MINI_CATALOG_DIR}"
    return MINI_CATALOG_DIR


@pytest.fixture(scope="session")
def mini_ref_dir() -> Path:
    assert MINI_REF_DIR.is_dir(), f"синтетическая фикстура не найдена: {MINI_REF_DIR}"
    return MINI_REF_DIR


def fake_head_always_200(url: str, timeout: float) -> int:  # noqa: ARG001 — сигнатура HeadFn
    return 200
