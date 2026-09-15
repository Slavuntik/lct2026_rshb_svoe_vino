PY := $(CURDIR)/.venv/bin/python

.PHONY: venv install all wines wineries articles \
        discover fetch sample parse normalize index report clean

venv:
	uv venv --python 3.12 .venv

install: venv
	uv pip install --python .venv/bin/python -r crawler/requirements.txt

# Полный цикл по всем сущностям.
all: wines wineries articles

# --- вина: единственная сущность с normalize и индексом ---

wines: discover fetch parse normalize index report

discover:
	cd crawler && $(PY) run.py discover --entity wines

fetch:
	cd crawler && $(PY) run.py fetch --entity wines --concurrency 4 --delay 0.7

sample:
	cd crawler && $(PY) run.py fetch --entity wines --limit 30 --delay 1.0

parse:
	cd crawler && $(PY) run.py parse --entity wines

normalize:
	cd crawler && $(PY) run.py normalize

index:
	cd crawler && $(PY) run.py build-index

report:
	cd crawler && $(PY) run.py report

# --- винодельни и статьи ---

wineries:
	cd crawler && $(PY) run.py discover --entity wineries
	cd crawler && $(PY) run.py fetch --entity wineries --concurrency 4 --delay 0.7
	cd crawler && $(PY) run.py parse --entity wineries

articles:
	cd crawler && $(PY) run.py discover --entity articles
	cd crawler && $(PY) run.py fetch --entity articles --concurrency 4 --delay 0.7
	cd crawler && $(PY) run.py parse --entity articles

# Перепарсить всё из raw/ без единого сетевого запроса.
reparse:
	cd crawler && $(PY) run.py parse --entity wines
	cd crawler && $(PY) run.py parse --entity wineries
	cd crawler && $(PY) run.py parse --entity articles
	cd crawler && $(PY) run.py normalize
	cd crawler && $(PY) run.py build-index
	cd crawler && $(PY) run.py report

clean:
	rm -rf build/* catalog/wines/*.json catalog/wineries/*.json catalog/articles/*.json
