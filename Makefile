# Воспроизводимый пайплайн WineScan. Команды — из корня репозитория.
# GPU выбирается переменной: make index GPU=3 (на общем сервере GPU 0 и 1 заняты).

PY ?= .venv/bin/python
GPU ?= 0
MODEL ?= google/siglip2-so400m-patch14-384
YAWS ?= -30,-15,0,15,30
# индексы сервиса: мультиракурсная галерея (WORKLOG, «Мультиракурсная галерея эталонов»)
INDEXES ?= siglip2-so400m-patch14-384__yaw-30_-15_0_15_30,siglip2-so400m-patch14-384__label__yaw-30_-15_0_15_30
RUN_GPU = CUDA_VISIBLE_DEVICES=$(GPU)

.PHONY: help install data catalog index index-frontal features artifacts synth cache eval scanner report serve participant test clean-eval

help:
	@echo "install     — .venv, torch (CUDA 12.6) и пакет с зависимостями ml, dev"
	@echo "data        — распаковать дамп Strapi и eval.zip (нужен unrar)"
	@echo "artifacts   — каталог, индексы SigLIP 2 (full, label, повороты $(YAWS)), SIFT-признаки эталонов"
	@echo "index-frontal — фронтальные индексы: базовая линия и сравнение галерей"
	@echo "synth       — синтетические выборки synth_v1 и synth_v2"
	@echo "cache       — кэш детекций и эмбеддингов запросов для офлайн-экспериментов"
	@echo "eval        — прогон поиска на synth_v2 и публичных фото + docs/RESULTS.md"
	@echo "scanner     — сквозной прогон сервиса (выбор рамки, проверка, отказ) + docs/RESULTS.md"
	@echo "serve       — сервис на :8080; participant — скрипт кейсодержателя на публичных фото"
	@echo "test        — unit-тесты (без GPU и данных)"

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
	.venv/bin/pip install -e ".[ml,dev]"

data:
	scripts/extract_dump.sh
	unzip -oq data/eval.zip -x '__MACOSX/*' -d data/eval
	cd data/eval && sha256sum -c checksums.sha256

catalog:
	$(PY) -m winescan.catalog.build --no-review

index:
	$(RUN_GPU) $(PY) -m winescan.search.build_index --model $(MODEL) --yaws=$(YAWS) --batch-size 32
	$(RUN_GPU) $(PY) -m winescan.search.build_index --model $(MODEL) --view label --yaws=$(YAWS) --batch-size 32

# фронтальные индексы: базовая линия; на них построены кэши synth_v1 / synth_v2 из WORKLOG
index-frontal:
	$(RUN_GPU) $(PY) -m winescan.search.build_index --model $(MODEL) --batch-size 16
	$(RUN_GPU) $(PY) -m winescan.search.build_index --model $(MODEL) --view label --batch-size 16

features:
	$(PY) -m winescan.search.local_features

artifacts: catalog index features

synth:
	$(PY) -m winescan.validation.build_synth --name synth_v1 --preset v1 --seed 1
	$(PY) -m winescan.validation.build_synth --name synth_v2 --preset v2 --seed 2

cache:
	$(RUN_GPU) $(PY) -m winescan.eval.query_cache --split synth_v2 --boxes 3

eval:
	$(RUN_GPU) $(PY) -m winescan.eval.run --split synth_v2 --index $(INDEXES) --crop detector --ocr --batch-size 16
	$(RUN_GPU) $(PY) -m winescan.eval.run --split public --index $(INDEXES) --crop detector --ocr
	$(MAKE) report

# сквозной прогон сервисного Scanner: решение «не найдено» и задержки, только отложенные запросы
scanner:
	$(RUN_GPU) $(PY) -m winescan.eval.scanner_eval --split synth_v2 --tag default --holdout
	$(RUN_GPU) $(PY) -m winescan.eval.scanner_eval --split public --tag default
	$(MAKE) report

report:
	$(PY) -m winescan.eval.report

serve:
	$(RUN_GPU) .venv/bin/uvicorn winescan.service.app:app --host 0.0.0.0 --port 8080

participant:
	rm -rf artifacts/eval/participant_public && mkdir -p artifacts/eval/participant_public
	cd data/eval && ./participant_test.sh --images-dir ./queries --manifest ./queries.tsv \
		--endpoint http://127.0.0.1:8080/v1/eval/predict \
		--output "$(CURDIR)/artifacts/eval/participant_public/predictions.jsonl"

test:
	$(PY) -m pytest -q
