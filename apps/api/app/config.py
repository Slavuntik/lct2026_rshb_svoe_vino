"""Конфигурация из env. Дефолты выбраны так, чтобы `uvicorn app.main:app`
поднимался одной командой без единого ключа: mock-LLM + mock-RAG + SQLite-файл
рядом с процессом.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import Request


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./svoy_somelye.db")
    )
    jwt_secret: str = field(
        # >=32 байта, чтобы PyJWT не сыпал InsecureKeyLengthWarning на HS256
        # в dev/тестах; для прода секрет обязателен через env JWT_SECRET.
        default_factory=lambda: os.environ.get("JWT_SECRET", "dev-insecure-secret-change-me-please-32b")
    )
    jwt_expires_seconds: int = field(
        default_factory=lambda: int(os.environ.get("JWT_EXPIRES_SECONDS", str(60 * 60 * 24)))
    )
    min_age_years: int = field(
        default_factory=lambda: int(os.environ.get("MIN_AGE_YEARS", "18"))
    )
    rag_provider: str = field(
        default_factory=lambda: os.environ.get("RAG_PROVIDER", "mock").strip().lower()
    )
    rag_index_version: str = field(
        default_factory=lambda: os.environ.get("RAG_INDEX_VERSION", "mock-fixtures-0.1")
    )
    # --- Кейс ЛЦТ: сканер по фото (contracts/image-scan.md v0.4) --------
    cv_provider: str = field(
        default_factory=lambda: os.environ.get("CV_PROVIDER", "mock").strip().lower()
    )
    label_verifier_provider: str = field(
        default_factory=lambda: os.environ.get("LABEL_VERIFIER_PROVIDER", "mock").strip().lower()
    )
    cv_index_version: str = field(
        default_factory=lambda: os.environ.get("CV_INDEX_VERSION", "mock-cv-fixtures-0.1")
    )
    cv_eval_report_path: str = field(
        # Дефолт — относительно cwd процесса; проект уже предполагает запуск
        # `uvicorn` из apps/api (см. DATABASE_URL=sqlite:///./... выше), так
        # что "../../packages/cv/eval/report.json" резолвится в корень репо.
        # Файла там пока нет (packages/cv не создан) — read_eval_report()
        # честно отдаёт None, см. app/cv/eval_report.py.
        default_factory=lambda: os.environ.get(
            "CV_EVAL_REPORT_PATH", "../../packages/cv/eval/report.json"
        )
    )
    cv_near_dup_gap_threshold: float = field(
        # Плейсхолдер до калибровки на датасете кейса (пересчитать вместе с
        # agents/G-cv.md, когда появится честный gap на реальном индексе).
        default_factory=lambda: float(os.environ.get("CV_NEAR_DUP_GAP_THRESHOLD", "0.05"))
    )
    cv_confident_score_threshold: float = field(
        # Тоже плейсхолдер — case.md не даёт числа, только "отрыв 1-го от
        # 2-го ощутимый и стабильный". Пересчитать по приезду датасета.
        default_factory=lambda: float(os.environ.get("CV_CONFIDENT_SCORE_THRESHOLD", "0.55"))
    )
    low_confidence_threshold: float = field(
        default_factory=lambda: float(os.environ.get("SCAN_LOW_CONFIDENCE_THRESHOLD", "0.6"))
    )
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.environ.get("SCAN_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))
    )
    rate_limit_window_seconds: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
    )
    rate_limit_max_requests: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "5"))
    )
    cors_origins: str = field(
        # v0.3 (ревью 02, п.6): дефолт — localhost dev-порты Vite (apps/web,
        # server.port=5173 в vite.config.ts; 4173 — `vite preview`), а не "*"
        # — так CORS реально что-то ограничивает, а не документирует намерение.
        # Прод-домен задаётся через env CORS_ORIGINS при деплое (см. infra/).
        default_factory=lambda: os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:4173,http://127.0.0.1:4173",
        )
    )


def get_settings() -> Settings:
    # Без кэширования: тесты меняют env через monkeypatch и создают приложение
    # заново на каждый тест (см. tests/conftest.py) — кэш здесь дал бы утечку
    # состояния между тестами.
    return Settings()


def get_settings_dep(request: Request) -> Settings:
    """FastAPI-зависимость: настройки, зафиксированные на момент create_app(),
    а не перечитанные заново на каждый запрос (см. app.state.settings)."""
    return request.app.state.settings
