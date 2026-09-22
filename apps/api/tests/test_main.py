"""app/main.py::create_app() — прогрев кэша подбора вина к блюду в фоновом
потоке (тимлид 22.09, после замера "холодный кэш 12 с на 2103 карточки",
reports/backend-dish-photo.md: "не должен доставаться первому пользователю —
на демо это выглядит как зависший запрос... в фоновом потоке, чтобы не
задерживать готовность сервиса"). Сама функция прогрева (`app/dish_pairing.py::
warm_up_catalog_cache`) протестирована в изоляции в tests/test_dish_pairing.py
— здесь только интеграция: `create_app()` действительно запускает фоновый
поток, который её вызывает, и возвращается сам НЕ дожидаясь его завершения.

Под pytest сам запуск потока ПРОПУСКАЕТСЯ (`PYTEST_CURRENT_TEST` — см.
докстринг main.py в месте прогрева): фоновый поток читает `CASE_DATA_DIR`
"живьём" не синхронно с созданием приложения, а pytest зовёт `create_app()`
сотни раз за прогон с быстро сменяющимся `CASE_DATA_DIR` — отставший поток
одного теста может дочитать до env уже следующего и заразить process-wide
`case_catalog._load_catalog()` (поймано эмпирически: 4 теста
test_wine_pairings.py стабильно падали при полном прогоне до этого фикса).
Тест ниже поэтому явно `monkeypatch.delenv("PYTEST_CURRENT_TEST", ...)` —
имитирует боевое условие ("не под pytest"), чтобы честно проверить, что
поток В ПРИНЦИПЕ запускается, а не только что guard существует."""
from __future__ import annotations

import threading
import time

from app import main as app_main


def test_create_app_warms_up_dish_pairing_catalog_in_background_thread(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)  # имитация "не под pytest" — см. докстринг файла

    called_with = []
    done = threading.Event()

    def fake_warm_up(retriever):
        called_with.append(retriever)
        done.set()
        return True

    monkeypatch.setattr(app_main, "warm_up_catalog_cache", fake_warm_up)

    t0 = time.monotonic()
    app = app_main.create_app()
    create_app_elapsed_s = time.monotonic() - t0

    assert done.wait(timeout=5.0), "фоновый прогрев не вызвался за разумное время"
    assert called_with == [app.state.retriever]
    # Ключевое: create_app() не ждёт прогрев (тот в отдельном потоке) — сам
    # вызов обязан быть быстрым, даже если бы прогрев был медленным (здесь
    # fake_warm_up мгновенный, но порядок событий — create_app() ДО done.set() —
    # уже доказан тем, что до ожидания done.wait() выше мы вышли из create_app()).
    assert create_app_elapsed_s < 5.0


def test_create_app_skips_background_warm_up_thread_under_pytest(monkeypatch):
    """Обратный случай — под pytest (`PYTEST_CURRENT_TEST` реально стоит, как
    и всегда во время прогона тестов) прогрев НЕ запускается вовсе: причина
    (гонка с monkeypatch.setenv соседних тестов) — докстринг файла/main.py."""
    assert "PYTEST_CURRENT_TEST" in __import__("os").environ, "тест ожидаемо запущен под pytest"

    called = []
    monkeypatch.setattr(app_main, "warm_up_catalog_cache", lambda retriever: called.append(1) or True)

    app_main.create_app()
    time.sleep(0.2)  # дать бы шанс фоновому потоку, если бы он всё-таки стартовал

    assert called == [], "под pytest фоновый прогрев не должен запускаться (гонка с monkeypatch соседних тестов)"


# Устойчивость к сбою прогрева (не роняет старт) — тест самой
# warm_up_catalog_cache() в изоляции, tests/test_dish_pairing.py::
# test_warm_up_catalog_cache_failure_returns_false_not_raises (try/except
# внутри неё — гарантия, что фоновый поток НИКОГДА не бросает наружу). Тест
# ЗДЕСЬ намеренно не подменяет её на версию "без защиты": такой сценарий
# структурно недостижим (единственный вызов в main.py идёт через уже
# защищённую функцию), а искусственное исключение в чужом потоке долетает
# до pytest как PytestUnhandledThreadExceptionWarning — шум без сигнала.
