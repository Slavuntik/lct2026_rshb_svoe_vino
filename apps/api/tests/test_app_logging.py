"""app/main.py — логгер пространства имён "app": ОДИН handler+INFO на весь
процесс (reports/backend-chat-retrieval.md, 22.09, п.4, решение тимлида поверх
находки reports/backend-text-source.md). Точечный handler routers/scan.py
поставил раньше — эта правка его убирает и переносит на уровень "app" целиком,
чтобы ЛЮБОЙ будущий `logging.getLogger(__name__).info(...)` где угодно в
apps/api долетал до вывода процесса без своего хендлера на каждый файл.

Критично: apps/api/tests/conftest.py::app зовёт create_app() НА КАЖДЫЙ тест
(сотни раз за прогон) — без idempotency-guard'а хендлеры копились бы и любая
INFO-запись печаталась бы N раз (дубли строк, которых явно просил избежать
тимлид)."""
from __future__ import annotations

import logging


def test_app_logger_has_info_level_and_exactly_one_handler(client):
    """`client`-фикстура (tests/conftest.py) уже вызвала create_app() один раз
    для ЭТОГО теста — плюс все предыдущие тесты в том же прогоне сделали то
    же самое. Один и тот же процесс/логгер "app" — хендлер обязан остаться
    ровно один, независимо от того, какой это по счёту create_app()."""
    app_logger = logging.getLogger("app")
    assert app_logger.level == logging.INFO
    assert len(app_logger.handlers) == 1


def test_repeated_create_app_does_not_add_more_handlers():
    from app.main import create_app

    before = len(logging.getLogger("app").handlers)
    for _ in range(5):
        create_app()
    after = len(logging.getLogger("app").handlers)

    assert before == 1
    assert after == 1, "повторные create_app() не должны копить хендлеры (дубли строк в логе)"


def test_child_logger_message_reaches_the_single_app_handler(caplog):
    """Функциональная проверка (не только структура): INFO-запись дочернего
    логгера (например app.routers.scan, app.chat.filters) реально доходит до
    обработки — через propagate на "app", без собственного хендлера модуля.
    caplog перехватывает независимо от количества хендлеров где-либо в цепочке
    (сам он подключается у root) — здесь проверяем именно propagate/уровень,
    не сам факт единственности хендлера (это тест выше)."""
    logger = logging.getLogger("app.routers.scan")
    with caplog.at_level(logging.INFO, logger="app.routers.scan"):
        logger.info("тестовая запись прогрева логирования")
    assert any(
        r.name == "app.routers.scan" and "тестовая запись" in r.getMessage()
        for r in caplog.records
    )


def test_scan_router_module_no_longer_installs_its_own_handler():
    """reports/backend-chat-retrieval.md (22.09, п.4): точечный handler в
    routers/scan.py убран — логгер модуля существует (для logger.info() на
    успешное чтение этикетки), но собственного handler'а не несёт, чтобы не
    дублировать вывод с общим "app"-хендлером (app/main.py)."""
    import app.routers.scan as scan_module

    assert scan_module.logger.name == "app.routers.scan"
    assert scan_module.logger.handlers == [], "handler должен жить только на 'app', не здесь"
