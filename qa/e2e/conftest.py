"""Инфраструктура для сквозных сценариев (агент F) против apps/web агента C.

По умолчанию — mock-режим (`VITE_API_MODE=mock`, MSW): самодостаточно, ничего руками
поднимать не нужно. `QA_STACK=real` (agents/F-qa-demo.md, приёмочный прогон волны 3)
поднимает ещё и настоящий `apps/api` — `RAG_PROVIDER=real RAG_MODE=embedded` (реальный
индекс agents/A-rag.md), `LLM_PROVIDER` не задан (дефолт `mock` — реального LLM-ключа нет,
см. qa/acceptance.md, PENDING), `DATABASE_URL` — приватный sqlite в scratch-директории (НЕ
`apps/api/svoy_somelye.db`: этот файл на момент прогона 2026-08-27 оказался читаем-только на
диске — см. qa/ACCEPTANCE-RUN-01.md, — трогать чужой рантайм-артефакт незачем, когда свой
одноразовый файл эквивалентен и безопаснее).

Если apps/web ещё не готов (`npm install`) или сервер(ы) не поднимаются — сессия e2e
корректно skip'ается с причиной, а не падает малопонятной ошибкой.

base_url переопределяет одноимённую фикстуру pytest-playwright/pytest-base-url — благодаря
этому в тестах можно писать `page.goto("/app/onboarding")` без склейки урла руками, в обоих
режимах одинаково.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

QA_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = QA_DIR.parent
WEB_APP_DIR = REPO_ROOT / "apps" / "web"
API_APP_DIR = REPO_ROOT / "apps" / "api"

QA_STACK = os.environ.get("QA_STACK", "mock").strip().lower()
if QA_STACK not in ("mock", "real"):
    QA_STACK = "mock"

# WEB_PORT — свой, не 5173, чтобы не сталкиваться с dev-сервером, который разработчик мог уже
# держать открытым у себя. API_PORT НЕ переопределяем — apps/web/vite.config.ts::server.proxy
# жёстко зашивает target "http://localhost:8000" (не читает env), а трогать чужой vite.config.ts
# ради удобства собственного прогона — не наша зона; поэтому в QA_STACK=real реальный API
# обязан слушать именно :8000, иначе прокси необходимо ловит связь.
#
# Хост — именно "localhost", не "127.0.0.1": на этой машине Vite слушает ТОЛЬКО IPv6-loopback
# ([::1]), а "127.0.0.1" (чистый IPv4) на LISTEN не отвечает вовсе — connection refused.
# "localhost" резолвится через getaddrinfo в тот же [::1], которым Vite реально владеет.
WEB_PORT = 5199
API_PORT = 8000
WEB_URL = f"http://localhost:{WEB_PORT}"
API_URL = f"http://localhost:{API_PORT}"
STARTUP_TIMEOUT_S = 30.0
API_STARTUP_TIMEOUT_S = 60.0  # реальный индекс грузит embedder/reranker в память — не мгновенно
LOG_DIR = QA_DIR / "e2e"


def _port_is_listening(port: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _wait_until_ready(url: str, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture(scope="session")
def api_server():
    """QA_STACK=real только: свой `uvicorn` (RAG_PROVIDER=real) на отдельном порту/БД.

    apps/api — чужая зона (агент B): здесь мы её только ЗАПУСКАЕМ как внешний процесс, с
    собственной приватной БД (см. модульный докстринг про read-only `svoy_somelye.db`,
    зафиксировано в qa/ACCEPTANCE-RUN-01.md) — ничего не пишем внутрь зоны B.
    """
    if not (API_APP_DIR / ".venv" / "bin" / "python").exists():
        pytest.skip(f"apps/api/.venv не найден — QA_STACK=real не может завестись без окружения B")

    if _port_is_listening(API_PORT):
        pytest.skip(f"Порт {API_PORT} уже занят — освободите и перезапустите")

    scratch_db = LOG_DIR / ".qa-real-run.db"
    scratch_db.unlink(missing_ok=True)
    env = {
        **os.environ,
        "RAG_PROVIDER": "real",
        "RAG_MODE": "embedded",
        "DATABASE_URL": f"sqlite:///{scratch_db}",
        # Каждый playwright-тест — новый browser context => новая гостевая регистрация
        # (helpers.complete_guest_onboarding). Дефолтный лимит apps/api (5 запросов/60с,
        # см. app/config.py) — корректная защита прод-эндпоинта, но она же оборвала бы
        # добрую половину этого прогона сама по себе, до какой-либо проверки функционала
        # (см. qa/ACCEPTANCE-RUN-01.md — так и произошло на первом прогоне). Здесь это
        # СВОЙ процесс, поднятый только для проверки функционала UI/данных, не поведения
        # rate-limiter'а (оно — забота тестов B, apps/api/tests/test_auth.py) — ослабляем
        # лимит через легальный env этого же приложения, не трогая код apps/api.
        "RATE_LIMIT_MAX_REQUESTS": "1000",
        # Реальный /scan/resolve считает low_confidence по АБСОЛЮТНОМУ порогу лучшего
        # совпадения (app/config.py::low_confidence_threshold, дефолт 0.6), не по разрыву
        # топ-2, как мок — на настоящих данных retriever почти всегда либо уверенно (>=0.6)
        # находит вино, либо не находит вовсе; сценарий "несколько кандидатов, никто явно не
        # лучше" (needed для e2e/test_scan_and_card.py::test_scan_text_ambiguous_...) без
        # искусственного повышения порога почти не воспроизводим. Поднимаем порог до 0.85
        # ТОЛЬКО для этого тестового сервера — легальный env самого приложения, не правка
        # apps/api; продовый дефолт 0.6 этим не затрагивается. См. helpers.py::_SCAN_FIXTURES.
        "SCAN_LOW_CONFIDENCE_THRESHOLD": "0.85",
    }
    log_path = LOG_DIR / ".api-server.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(API_APP_DIR / ".venv" / "bin" / "uvicorn"), "app.main:app", "--port", str(API_PORT)],
        cwd=API_APP_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    ready = _wait_until_ready(API_URL + "/v1/healthz", API_STARTUP_TIMEOUT_S)
    if not ready:
        _terminate(process)
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path.exists() else "(лога нет)"
        pytest.skip(f"apps/api (RAG_PROVIDER=real) не поднялся за {API_STARTUP_TIMEOUT_S:.0f}с. Хвост лога:\n{tail}")
    try:
        yield API_URL
    finally:
        _terminate(process)
        log_file.close()


@pytest.fixture(scope="session")
def dev_server(request):
    """Поднимает `npm run dev` из apps/web. QA_STACK=mock (по умолчанию) — MSW, ничего
    больше не нужно. QA_STACK=real — VITE_API_MODE=real, требует api_server (proxy /v1 на
    его порт, см. apps/web/vite.config.ts, PROXY_TARGET читается из env для этого прогона).
    """
    if not WEB_APP_DIR.is_dir():
        pytest.skip(f"apps/web не найден по {WEB_APP_DIR} — e2e против клиента C пропущены")
    if not (WEB_APP_DIR / "node_modules").is_dir():
        pytest.skip(
            "apps/web/node_modules отсутствует (не сделан `npm install` в зоне агента C) — "
            "e2e-спека валидна как контракт, но не может завестись без сборки клиента; skip."
        )
    if _port_is_listening(WEB_PORT):
        pytest.skip(f"Порт {WEB_PORT} уже занят — освободите и перезапустите")

    env = {**os.environ, "VITE_API_MODE": QA_STACK}
    if QA_STACK == "real":
        # api_server слушает жёстко :8000, ровно то, что вшито в vite.config.ts — прокси
        # находит его без какой-либо переменной окружения (её там просто нет, см. модуль).
        request.getfixturevalue("api_server")

    log_path = LOG_DIR / ".dev-server.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(WEB_PORT), "--strictPort"],
        cwd=WEB_APP_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    ready = _wait_until_ready(WEB_URL + "/", STARTUP_TIMEOUT_S)
    if not ready:
        _terminate(process)
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:] if log_path.exists() else "(лога нет)"
        pytest.skip(f"dev-сервер apps/web не поднялся за {STARTUP_TIMEOUT_S:.0f}с. Хвост лога:\n{log_tail}")

    try:
        yield WEB_URL
    finally:
        _terminate(process)
        log_file.close()


@pytest.fixture(scope="session")
def base_url(dev_server) -> str:
    """Переопределяет фикстуру pytest-playwright/pytest-base-url — page.goto("/x") в тестах
    резолвится относительно поднятого здесь dev-сервера, в обоих режимах одинаково."""
    return dev_server
