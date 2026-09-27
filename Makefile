# Свой Сомелье — корневой Makefile.
# Зона qa/ (агент F, agents/F-qa-demo.md) — основная: демо-паки, e2e, оценка сканера.
# Другие агенты видят этот файл, но не пишут в него (ORCHESTRATION.md, «своя зона»).
#
# Исключение появилось вместе со вторым движком распознавания: пакет packages/winescan
# приехал из отдельного репозитория и синхронизируется скриптом, а не руками, поэтому его
# цели живут здесь же — отдельным блоком в конце файла, ничего не меняя в зоне qa/.
#
# Главная цель — DoD агента F: демо-пак одной командой.
#   make demo-pack WINERY=abrau-dyurso
#
# uv — по умолчанию из ~/.local/bin (см. ORCHESTRATION.md); переопределяемо через UV=...
# для машин, где uv уже в PATH.
UV ?= $(HOME)/.local/bin/uv
WINERY ?= abrau-dyurso
PYTHON := qa/.venv/bin/python

.PHONY: qa-venv demo-pack demo-pack-abrau demo-pack-second demo-pack-all \
        qa-test qa-test-network e2e-install qa-e2e qa-e2e-real qa-test-all qa-clean \
        scan-eval-mock scan-mock-server case-script-rehearse \
        winescan-test winescan-sync-check winescan-sync

## Однократная установка окружения qa/ (Python 3.12 через uv, см. qa/requirements.txt).
## Идемпотентна: повторный запуск не ломает уже готовое окружение.
qa-venv:
	@test -x "$(UV)" || (echo "uv не найден по $(UV) — поставь uv или передай UV=<путь>" >&2; exit 1)
	@test -d qa/.venv || (cd qa && $(UV) venv --python 3.12 .venv)
	cd qa && $(UV) pip install -r requirements.txt

## Демо-пак одной командой: make demo-pack WINERY=abrau-dyurso
## Выход: qa/packs/<winery>/pack.md (человеку) и pack.json (машине).
demo-pack: qa-venv
	$(PYTHON) qa/demo_pack.py --winery $(WINERY)

## Оба демонстрационных пака разом: первый (Абрау-Дюрсо) + второй, для проверки
## универсальности генератора на произвольной винодельне (agents/F-qa-demo.md).
demo-pack-abrau: qa-venv
	$(PYTHON) qa/demo_pack.py --winery abrau-dyurso

demo-pack-second: qa-venv
	$(PYTHON) qa/demo_pack.py --winery alma-valley

demo-pack-all: demo-pack-abrau demo-pack-second

## pytest на demo_pack.py (генерация паков, автопроверка, сломанная карточка-фикстура) и на
## scan_eval.py (метрики/сплит/загрузчик кейса-сканера, qa/test_scan_eval.py — свои тесты
## HTTP гоняют против localhost-мока qa/mock_scan_server.py, реальную сеть не трогают, так
## что живут в том же "быстром" таргете, что и demo_pack). Сеть НЕ используется (network-тест
## исключён отдельно, см. qa-test-network) — быстрый прогон, детерминированный, годится для
## частого локального запуска.
qa-test: qa-venv
	$(PYTHON) -m pytest -q qa/test_demo_pack.py qa/test_scan_eval.py -m "not network"

## Те же файлы, но включая один тест с реальными вежливыми HEAD на vino-svoe.ru
## (разрешено брифом агента F явно, см. "Не делать").
qa-test-network: qa-venv
	$(PYTHON) -m pytest -q qa/test_demo_pack.py qa/test_scan_eval.py

## Playwright ставит бинарник Chromium один раз (~250 МБ, нужна сеть) — отдельная цель,
## чтобы qa-test не тянул это за собой на каждый прогон.
e2e-install: qa-venv
	$(PYTHON) -m playwright install chromium

## Сквозные сценарии qa/e2e/ (Playwright) против apps/web в mock-режиме клиента C.
## Сама фикстура поднимает и гасит `npm run dev` — apps/web должен быть `npm install`-нут
## заранее (чужая зона, не трогаем).
qa-e2e: e2e-install
	$(PYTHON) -m pytest -q qa/e2e

## То же самое, но против настоящего API (RAG_PROVIDER=real, mock-LLM) через vite-proxy —
## приёмочный прогон волны 3 (qa/ACCEPTANCE-RUN-01.md). Требует apps/api/.venv с extra
## "integration" (cd apps/api && uv sync --extra dev --extra integration, зона B) и собранный
## индекс packages/rag/data/manifest.json (зона A). Те же 26/26, что и qa-e2e — тесты
## mode-aware (см. qa/e2e/helpers.py), никаких skip/xfail по режиму.
qa-e2e-real: e2e-install
	QA_STACK=real $(PYTHON) -m pytest -q qa/e2e

## Все тесты agents/F-qa-demo.md разом: demo_pack (включая сетевой кейс) + e2e в ОБОИХ
## режимах (mock и real).
qa-test-all: qa-test-network qa-e2e qa-e2e-real

## --- Кейс ЛЦТ — сканер (qa/scan_eval.py, contracts/image-scan.md) --------------------
## Датасет и скрипт оценки кейсодержателя приезжают локально в CASE_DATA_DIR (не в git,
## см. .gitignore) — до тех пор эти цели работают на синтетике qa/tests/fixtures/scan_mini.
## Против настоящих данных: PHOTOS_DIR=$$CASE_DATA_DIR/public make scan-eval-mock (или свой
## --api-url/--mode, см. qa/scan_eval.py --help).

PHOTOS_DIR ?= qa/tests/fixtures/scan_mini

## Без сети и без сервера: MockPredictor (по умолчанию — идеальный оракул) на фикстуре —
## быстрый смоук-прогон механики loader -> метрики -> отчёт (JSON+MD в qa/scan-eval-runs/).
scan-eval-mock: qa-venv
	$(PYTHON) qa/scan_eval.py --photos-dir $(PHOTOS_DIR) --mode mock --split all

## Свой HTTP-мок POST /v1/scan/photo (без CV, отвечает по имени файла — см. докстринг
## qa/mock_scan_server.py) — для рехёрсала qa/mock_case_script.sh или qa/scan_eval.py
## --mode flat|rich --api-url http://localhost:8100. Foreground — Ctrl-C, чтобы остановить.
scan-mock-server: qa-venv
	$(PYTHON) qa/mock_scan_server.py --port 8100 --map qa/tests/fixtures/scan_mini/mock_map.json

## Рехёрсал скрипта оценки кейсодержателя (case.md, п.6) — требует, чтобы scan-mock-server
## (или настоящий apps/api) уже был поднят в отдельном терминале; см. докстринг
## qa/mock_case_script.sh про переменную API_URL (по умолчанию тут — localhost:8100, порт
## scan-mock-server; для настоящего API: API_URL=http://localhost:8000 make case-script-rehearse).
case-script-rehearse:
	API_URL=$${API_URL:-http://localhost:8100} qa/mock_case_script.sh $(PHOTOS_DIR)

## Генерированные паки — не исходники: чистая пересборка перед показом.
qa-clean:
	rm -rf qa/packs qa/scan-eval-runs

## --- Пакет winescan: второй движок распознавания (docs/scan-engines.md) ---------------
## Исходный проект сохранён в standalone/winescan: правки кода делаются там,
## в packages/winescan переносятся скриптом. Путь к источнику НЕ зашит в Makefile —
## умолчание живёт в самом скрипте, а переменная передаётся, только если её задали:
##   WINESCAN_SOURCE=/путь/к/репозиторию make winescan-sync-check
WINESCAN_PY ?= python3
WINESCAN_SOURCE ?=
WINESCAN_SOURCE_ARG = $(if $(WINESCAN_SOURCE),--source "$(WINESCAN_SOURCE)")

## Тесты пакета: ни GPU, ни моделей, ни данных кейса не требуют.
## Кавычки обязательны: путь к интерпретатору может содержать пробел (проверено — без них
## оболочка обрывает его на первом пробеле и цель падает с «not found»).
winescan-test:
	PYTHONPATH=packages/winescan "$(WINESCAN_PY)" -m pytest -q packages/winescan/tests

## Что разошлось между пакетом и его источником (ненулевой код возврата — есть расхождения).
winescan-sync-check:
	$(WINESCAN_PY) tools/sync_winescan.py --check $(WINESCAN_SOURCE_ARG)

## Перенести свежее состояние источника; коммит источника пишется в packages/winescan/SYNC.md.
winescan-sync:
	$(WINESCAN_PY) tools/sync_winescan.py --apply $(WINESCAN_SOURCE_ARG)

## --- Quickstart для жюри: локальный запуск одной командой (scripts/quickstart.sh) ----
## Задача тимлида 27.09 (см. reports/devops-quickstart.md) — отдельный блок, ничего не
## меняет в целях выше (тот же принцип, что и у блока winescan). Подробности и формат
## данных кейса — docs/QUICKSTART.md.
.PHONY: quickstart quickstart-full quickstart-verify quickstart-status quickstart-stop

## По умолчанию: реальный поиск сомелье + сканер/LLM-заглушки, минуты, без скачиваний.
quickstart:
	bash scripts/quickstart.sh fast

## Настоящее распознавание: нужен CASE_DATA_DIR, качает модели, строит индекс — десятки минут.
quickstart-full:
	bash scripts/quickstart.sh full

## Официальный eval/participant_test.sh против локального API (нужен уже поднятый quickstart).
quickstart-verify:
	bash scripts/quickstart.sh verify

quickstart-status:
	bash scripts/quickstart.sh status

quickstart-stop:
	bash scripts/quickstart.sh stop
