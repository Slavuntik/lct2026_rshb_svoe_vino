"""Конфигурация CV-ядра. Всё — через env, разумные дефолты под dev-машину.

Данные vines и датасет кейса — read-only, этот модуль их не пишет, только читает пути.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # packages/cv

# --- Данные кейса (contracts/image-scan.md) -----------------------------------------
# Приезжают отдельно и позже (не в этой волне) — путь объявлен уже сейчас, чтобы
# код/CLI, который будет их читать, не пришлось потом чинить по всему пакету.
CASE_DATA_DIR = Path(os.environ.get("CASE_DATA_DIR", "/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data"))

# --- Дев-фикстуры (НЕ датасет кейса, см. scripts/fetch_devfix.py) -------------------
DEVFIX_DIR = Path(os.environ.get("CV_DEVFIX_DIR", str(PACKAGE_ROOT / "devfix")))

# --- Собственное хранилище (пишем только сюда) --------------------------------------
DATA_DIR = Path(os.environ.get("CV_DATA_DIR", str(PACKAGE_ROOT / "data")))
QDRANT_PATH = Path(os.environ.get("CV_QDRANT_PATH", str(DATA_DIR / "qdrant")))
MANIFEST_PATH = Path(os.environ.get("CV_MANIFEST_PATH", str(DATA_DIR / "manifest.json")))
EMBED_CACHE_DIR = Path(os.environ.get("CV_EMBED_CACHE_DIR", str(PACKAGE_ROOT / ".embed_cache")))
AUGMENT_CACHE_DIR = Path(os.environ.get("CV_AUGMENT_CACHE_DIR", str(DATA_DIR / "augmented")))

# --- Бэкенд индекса: контракт "IMAGE_INDEX_MODE=qdrant_embedded | pgvector" ---------
# Реализован только qdrant_embedded — pgvector контракт сам называет "прод-профилем
# в compose" (см. contracts/image-scan.md); Docker на этой машине нет, вне бюджета
# этой части. Интерфейс QdrantStore зеркалит packages/rag/rag/store.py: тот же класс
# обслуживает и embedded (path=), и сетевой Qdrant (QDRANT_URL) — так что "переезд на
# прод Qdrant" — тоже строчка env, без Docker уже сейчас можно не переживать за это.
IMAGE_INDEX_MODE = os.environ.get("IMAGE_INDEX_MODE", "qdrant_embedded")
QDRANT_URL = os.environ.get("QDRANT_URL") or None
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
COLLECTION_NAME = os.environ.get("CV_COLLECTION", "cv_image_views")

# --- Энкодер (выбор чекпойнта и обоснование — reports/g-report.md, раздел "Модель") -
CV_MODEL = os.environ.get("CV_MODEL", "google/siglip2-base-patch16-224")
CV_DEVICE = os.environ.get("CV_DEVICE") or None  # None -> автоопределение (mps > cuda > cpu)

# --- Аугментатор (cv/augment.py) -----------------------------------------------------
AUGMENT_VIEWS_PER_REF = int(os.environ.get("CV_AUGMENT_VIEWS", "24"))  # DoD: >= 20
AUGMENT_OUT_SIZE = int(os.environ.get("CV_AUGMENT_OUT_SIZE", "448"))
AUGMENT_SEED_DEFAULT = int(os.environ.get("CV_AUGMENT_SEED", "0"))

# --- Поиск (cv/index.py) -------------------------------------------------------------
# ANN идёт по РАКУРСАМ (много точек на позицию), поэтому top_k нужно оверфетчить,
# иначе после схлопывания в позиции на выходе может остаться меньше top_k уникальных
# slug'ов, чем попросили (несколько верхних мест ANN займут разные ракурсы одной же
# позиции).
SEARCH_OVERFETCH = int(os.environ.get("CV_SEARCH_OVERFETCH", "8"))
# Порог "та же группа кандидатов" для Match.gap (score — косинусная близость
# [-1, 1], т.к. векторы L2-нормированы и коллекция Qdrant на Distance.COSINE).
# Near-dup позиции (одна этикетка, разные год/категория) визуально почти
# неотличимы для CV — их скор естественно попадает в этот эпсилон друг от друга.
GROUP_EPSILON = float(os.environ.get("CV_GROUP_EPSILON", "0.03"))

INDEX_VERSION_DEFAULT = "dev"
