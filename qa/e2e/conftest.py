"""Инфраструктура для сквозных сценариев (агент F) против apps/web агента C, mock-режим.

Поднимает `npm run dev` (VITE_API_MODE=mock) как часть тестовой сессии — самодостаточно,
ничего руками поднимать не нужно перед `pytest qa/e2e`. Если apps/web ещё не готов (нет
`npm install`) или dev-сервер не поднимается — вся сессия e2e корректно skip'ается с
причиной (agents/F-qa-demo.md: «сценарии — как исполняемая спецификация со skip»),
а не падает малопонятной ошибкой.

base_url переопределяет одноимённую фикстуру pytest-playwright/pytest-base-url — благодаря
этому в тестах можно писать `page.goto("/app/onboarding")` без склейки урла руками.
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

# Отдельный порт, а не 5173 из apps/web/vite.config.ts — чтобы не сталкиваться с dev-сервером,
# который разработчик (агент C или Вячеслав) мог уже держать открытым на 5173 у себя.
#
# Хост — именно "localhost", не "127.0.0.1": на этой машине Vite слушает ТОЛЬКО IPv6-loopback
# ([::1]), а "127.0.0.1" (чистый IPv4) на LISTEN не отвечает вовсе — connection refused.
# "localhost" резолвится через getaddrinfo в тот же [::1], которым Vite реально владеет.
DEV_SERVER_PORT = 5199
DEV_SERVER_URL = f"http://localhost:{DEV_SERVER_PORT}"
STARTUP_TIMEOUT_S = 30.0
LOG_PATH = QA_DIR / "e2e" / ".dev-server.log"


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


@pytest.fixture(scope="session")
def dev_server():
    """Поднимает `npm run dev -- --port 5199 --strictPort` из apps/web в mock-режиме.

    apps/web — чужая зона (агент C): здесь мы её только ЗАПУСКАЕМ как внешний процесс,
    ничего не пишем внутрь. Если `npm install` там ещё не делали — skip с понятной причиной,
    а не попытка молча его выполнить (агент F не правит чужую зону).
    """
    if not WEB_APP_DIR.is_dir():
        pytest.skip(f"apps/web не найден по {WEB_APP_DIR} — e2e против клиента C пропущены")

    if not (WEB_APP_DIR / "node_modules").is_dir():
        pytest.skip(
            "apps/web/node_modules отсутствует (не сделан `npm install` в зоне агента C) — "
            "e2e-спека валидна как контракт, но не может завестись без сборки клиента; skip."
        )

    if _port_is_listening(DEV_SERVER_PORT):
        pytest.skip(
            f"Порт {DEV_SERVER_PORT} уже занят — похоже, dev-сервер e2e уже где-то запущен "
            f"(или порт занят чем-то другим). Освободите {DEV_SERVER_PORT} и перезапустите."
        )

    env = {**os.environ, "VITE_API_MODE": "mock"}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("w", encoding="utf-8")

    process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(DEV_SERVER_PORT), "--strictPort"],
        cwd=WEB_APP_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,  # свой process group — чтобы погасить и дочерний vite тоже
    )

    ready = _wait_until_ready(DEV_SERVER_URL + "/", STARTUP_TIMEOUT_S)
    if not ready:
        _terminate(process)
        log_tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-2000:] if LOG_PATH.exists() else "(лога нет)"
        pytest.skip(
            f"dev-сервер apps/web не поднялся за {STARTUP_TIMEOUT_S:.0f}с на {DEV_SERVER_URL} — "
            f"e2e пропущены. Хвост лога:\n{log_tail}"
        )

    try:
        yield DEV_SERVER_URL
    finally:
        _terminate(process)
        log_file.close()


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
def base_url(dev_server) -> str:
    """Переопределяет фикстуру pytest-playwright/pytest-base-url — page.goto("/x") в тестах
    резолвится относительно поднятого здесь dev-сервера."""
    return dev_server
