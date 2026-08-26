"""Конфигурация RAG-ядра. Всё — через env, с разумными дефолтами под dev-машину.

Ничего не пишем в данные vines (read-only) — только читаем.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # packages/rag

# --- Источник данных (vines, read-only) ---------------------------------
VINES_ROOT = Path(os.environ.get("RAG_VINES_ROOT", "/Users/vyacheslavfokin/ClaudeWorkspace/vines"))
BUILD_DIR = Path(os.environ.get("RAG_BUILD_DIR", str(VINES_ROOT / "build")))
CATALOG_DIR = Path(os.environ.get("RAG_CATALOG_DIR", str(VINES_ROOT / "catalog")))
REF_DIR = Path(os.environ.get("RAG_REF_DIR", str(VINES_ROOT / "ref")))

# --- Собственное хранилище индекса (пишем только сюда) ------------------
DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", str(PACKAGE_ROOT / "data")))
QDRANT_PATH = Path(os.environ.get("RAG_QDRANT_PATH", str(DATA_DIR / "qdrant")))
# Прод: Qdrant — отдельный сервис (compose). Если задан QDRANT_URL — работаем
# по сети (QdrantClient(url=...)); иначе embedded path (dev-дефолт, см. выше).
QDRANT_URL = os.environ.get("QDRANT_URL") or None
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY") or None
PAYLOADS_DIR = DATA_DIR / "payloads"
BM25_DIR = DATA_DIR / "bm25"
LABELS_PATH = DATA_DIR / "labels.jsonl"
MANIFEST_PATH = DATA_DIR / "manifest.json"

# --- Модели ---------------------------------------------------------------
# ВЫБОР МОДЕЛИ (см. reports/a-report.md, раздел "Модели"):
# контракт называет bge-m3, но в установленной версии fastembed (0.8.0)
# BAAI/bge-m3 отсутствует в списке поддерживаемых TextEmbedding-моделей
# (TextEmbedding.list_supported_models()) — интеграция потребовала бы
# ручной регистрации кастомной ONNX-модели, что не входит в разумный
# бюджет задачи. Взята меньшая мультиязычная модель из того же реестра
# fastembed — она официально поддерживается, тянется с HF Hub и даёт
# ~240 текстов/сек на CPU этой машины (см. замеры в отчёте).
FASTEMBED_CACHE_DIR = Path(os.environ.get("RAG_FASTEMBED_CACHE", str(PACKAGE_ROOT / ".fastembed_cache")))
DENSE_MODEL_NAME = os.environ.get(
    "RAG_DENSE_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
DENSE_DIM = int(os.environ.get("RAG_DENSE_DIM", "384"))

# Реранкер bge-reranker-v2-m3 недоступен в fastembed 0.8.0 (нет в реестре
# TextCrossEncoder.list_supported_models()). Взята мультиязычная альтернатива
# jinaai/jina-reranker-v2-base-multilingual (1.1GB, кросс-энкодер, понимает RU).
# Первая попытка скачивания упёрлась в таймаут 100с (медленная сеть до HF),
# повторная фоновая попытка докачала модель за ~112с — сеть просто медленная,
# не заблокирована. Дальше модель кэшируется локально, инференс top-50 ~0.6с
# на CPU этой машины (см. замеры в отчёте). Пустая строка = реранкер выключен
# (тогда пайплайн — dense+BM25 RRF без реранка, это тоже полностью валидный
# режим по брифу "если скачивание срывается — работай на dense-only").
RERANKER_MODEL_NAME = os.environ.get("RAG_RERANKER_MODEL", "jinaai/jina-reranker-v2-base-multilingual")

# Ограничение потоков onnxruntime. Машина без Docker используется параллельно
# несколькими агентами — без лимита onnxruntime разбирает все ядра под себя,
# и на общей машине это оборачивается конкуренцией/трэшингом (на прогоне eval
# system-time вырос до кратности к user-time). None = дефолт onnxruntime
# (все ядра) — используйте на выделенной машине/в проде.
ONNX_THREADS = int(os.environ.get("RAG_ONNX_THREADS", "4")) or None

# --- Пайплайн поиска -------------------------------------------------------
COLLECTIONS = ("wines", "knowledge", "wineries")
CANDIDATE_POOL = int(os.environ.get("RAG_CANDIDATE_POOL", "50"))  # top-N перед RRF-фьюжном
RRF_K = int(os.environ.get("RAG_RRF_K", "60"))  # константа RRF (канон = 60)

# Реранкер — самая дорогая стадия на CPU: замер на реальном каталоге показал,
# что стоимость определяет ДЛИНА документа, а не только их число (полный
# текст статьи до ~4000 симв. -> ~10с на 50 кандидатов; обрезка до 500 симв.
# даёт ~1.3с). RERANK_POOL режет и число кандидатов, идущих в кросс-энкодер
# (top-20 из уже отранжированных RRF top-50, канон контракта допускает top-20
# как план Б — см. риск 1 ревью 01), остальные из пула сохраняют RRF-порядок
# и остаются "ниже" реранкнутых. Подробные цифры — в отчёте.
RERANK_POOL = int(os.environ.get("RAG_RERANK_POOL", "20"))
RERANK_TRUNCATE_CHARS = int(os.environ.get("RAG_RERANK_TRUNCATE_CHARS", "500"))

# --- Fuzzy-пороги (resolve_label / resolve_style) --------------------------
# Скорер: rapidfuzz.fuzz.token_set_ratio, НЕ WRatio. Замер на реальном
# labels-корпусе (1978 вин) показал, что WRatio даёт ложные срабатывания
# 85+ на семантически несвязанных фразах («погода в москве завтра» -> 85.5,
# чистая случайность на уровне символов через partial_ratio) — риск, которым
# явно предупреждал бриф («мусор -> пусто»). token_set_ratio на том же
# корпусе развёл честные и мусорные запросы куда чище: точное совпадение
# ~100, опечатка ~79, часть текста этикетки (с посторонним текстом) ~67,
# любой мусор — не выше ~45. Подробности и цифры — в отчёте.
#
# Ниже GARBAGE — считаем, что вход не про вино/стиль, возвращаем пусто.
# Между GARBAGE и CONFIDENT — возвращаем кандидатов, но с meta["low_confidence"]=True.
# Скорер применяется с rapidfuzz.utils.default_process (lower-case + только
# буквы/цифры/пробелы) — иначе КАПС/латиница на этикетке (частый случай OCR:
# "ABRAU-DURSO" вместо "Абрау-Дюрсо") резко занижает скор без всякой пользы.
# Замер worst-case мусора (сленговые фразы вроде «привет как дела») дал 50.0,
# честный «шумный» текст этикетки — 55-78; граница 52 разделяет их с запасом.
LABEL_GARBAGE_CUTOFF = int(os.environ.get("RAG_LABEL_GARBAGE_CUTOFF", "52"))
LABEL_CONFIDENT_CUTOFF = int(os.environ.get("RAG_LABEL_CONFIDENT_CUTOFF", "75"))
STYLE_GARBAGE_CUTOFF = int(os.environ.get("RAG_STYLE_GARBAGE_CUTOFF", "55"))

INDEX_VERSION_DEFAULT = "dev"
