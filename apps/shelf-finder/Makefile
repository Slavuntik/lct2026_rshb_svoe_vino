PYTHON ?= python3.12
PY = server/.venv/bin/python
GPU ?= 2

.PHONY: server-install server-test serve web build
server-install:
	$(PYTHON) -m venv server/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e 'server[ml,test]'
server-test:
	$(PY) -m pytest -c server/pyproject.toml server/tests -q
serve:
	CUDA_VISIBLE_DEVICES=$(GPU) SHELF_STATIC_DIR="$(CURDIR)/dist" $(PY) -m uvicorn shelf_api.app:app --host 0.0.0.0 --port 8086 --workers 1
web:
	npm run dev
build:
	npm run build
