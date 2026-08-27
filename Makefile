# Свой Сомелье — корневой Makefile.
# Единственная зона, откуда он управляет вещами, — qa/ (агент F, agents/F-qa-demo.md).
# Другие агенты видят этот файл, но не пишут в него (ORCHESTRATION.md, «своя зона»).
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
        qa-test qa-test-network e2e-install qa-e2e qa-test-all qa-clean

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

## pytest на demo_pack.py: генерация паков, автопроверка, сломанная карточка-фикстура.
## Сеть НЕ используется (network-тест исключён отдельно, см. qa-test-network) — быстрый
## прогон, детерминированный, годится для частого локального запуска.
qa-test: qa-venv
	$(PYTHON) -m pytest -q qa/test_demo_pack.py -m "not network"

## Тот же файл, но включая один тест с реальными вежливыми HEAD на vino-svoe.ru
## (разрешено брифом агента F явно, см. "Не делать").
qa-test-network: qa-venv
	$(PYTHON) -m pytest -q qa/test_demo_pack.py

## Playwright ставит бинарник Chromium один раз (~250 МБ, нужна сеть) — отдельная цель,
## чтобы qa-test не тянул это за собой на каждый прогон.
e2e-install: qa-venv
	$(PYTHON) -m playwright install chromium

## Сквозные сценарии qa/e2e/ (Playwright) против apps/web в mock-режиме клиента C.
## Сама фикстура поднимает и гасит `npm run dev` — apps/web должен быть `npm install`-нут
## заранее (чужая зона, не трогаем).
qa-e2e: e2e-install
	$(PYTHON) -m pytest -q qa/e2e

## Все тесты agents/F-qa-demo.md разом (demo_pack + e2e, включая сетевой кейс).
qa-test-all: qa-test-network qa-e2e

## Генерированные паки — не исходники: чистая пересборка перед показом.
qa-clean:
	rm -rf qa/packs
