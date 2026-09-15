from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEVFIX_DIR = PACKAGE_ROOT / "devfix"
NEAR_DUP_SLUGS = ("aligote-barrel-2024", "aligote-barrel-2025")


@pytest.fixture(scope="session")
def devfix_dir() -> Path:
    """Дев-фикстуры (см. scripts/fetch_devfix.py) — НЕ датасет кейса, скачаны локально
    для self-match/замеров. Гитигнорены — на чистом клоне их нет, тесты, которым они
    нужны, аккуратно скипаются, а не падают."""
    if not DEVFIX_DIR.exists() or not any(DEVFIX_DIR.glob("*.webp")):
        pytest.skip("Дев-фикстуры не скачаны: python3 scripts/fetch_devfix.py")
    return DEVFIX_DIR


@pytest.fixture()
def synthetic_bottle_image() -> np.ndarray:
    """Маленькое синтетическое "фото бутылки" для тестов, не завязанных на реальные
    фикстуры (детерминизм/размеры аугментатора и т.п.) — не сеть, не диск."""
    rng = np.random.default_rng(42)
    img = np.full((300, 140, 3), 40, dtype=np.uint8)  # тёмное "стекло"
    img[180:260, 15:125] = rng.integers(80, 220, size=(80, 110, 3), dtype=np.uint8)  # цветная "этикетка"
    return img
