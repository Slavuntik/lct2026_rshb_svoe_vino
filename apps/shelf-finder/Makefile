PYTHON ?= python3.12
PY = server/.venv/bin/python
GPU ?= 2
DEVICE ?= auto

.PHONY: server-install server-install-cpu server-test serve web build
server-install:
	$(PYTHON) -m venv server/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e 'server[ml,vlm,test]'
server-test:
	$(PY) -m pytest -c server/pyproject.toml server/tests -q
serve:
	CUDA_VISIBLE_DEVICES=$(GPU) SHELF_DEVICE=$(DEVICE) SHELF_STATIC_DIR="$(CURDIR)/dist" $(PY) -m uvicorn shelf_api.app:app --host 0.0.0.0 --port 8086 --workers 1
web:
	npm run dev
build:
	npm run build

# CPU wheels avoid pulling CUDA runtime packages on a CPU-only host.
server-install-cpu:
	$(PYTHON) -m venv server/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install torch --index-url https://download.pytorch.org/whl/cpu
	$(PY) -m pip install -e 'server[ml,vlm,test]'
