# Прогон: синтетический baseline на боевом индексе case-20260917

Полномасштабный (1982/1982 usable-слагов, 100%) прогон `qa/scan_eval.py --mode rich`
против ЖИВОГО `apps/api` (`IMAGE_PROVIDER=real VERIFIER_PROVIDER=real`, индекс
`case-20260917`, 49650 векторов). Поручение оркестратора, ночь 2026-09-17, поверх
`agents/F3-census.md`. Полный разбор — `reports/f3-synthetic-baseline.md`.

**Это НЕ полевой замер.** Каждое фото — ОДИН свежий синтетический ракурс (seed=20260917,
`qa/gen_case_synthetic_baseline_photos.py`, `packages/cv/cv/augment.py`) из ТОГО ЖЕ
эталона, что и в индексе (self-match-style на новом ракурсе). Полевой замер — по приезду
публичного датасета (`qa/acceptance.md` §9).

## Файлы

- `report.json` — объединённые records обоих батчей (A+B, см. ниже) + агрегатные метрики
  + `case_breakdown` (near-dup/fallback разрезы) + `raw_top1_rate_ungated_overall`.
- `report.md` — человекочитаемая версия (авто-генерация `scan_eval.py::render_report_md`).
- `eval_report_snapshot.json` — снимок в схеме `apps/api/app/cv/eval_report.py`
  (`CV_EVAL_REPORT_PATH`), подтверждено живым запросом, что `GET /v1/metrics/scan`
  отдаёт эти же числа.

Синтетические фото (1982 шт., scratchpad, НЕ в git) сюда не попадают — только числа.

## Воспроизведение

```bash
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye

# 1. Синтетика — 1 свежий ракурс на usable-слаг (~100с на 1982 фото)
packages/cv/.venv/bin/python qa/gen_case_synthetic_baseline_photos.py \
    --out-dir <SCRATCH>/case-synthetic-baseline

# 2. API на реальном индексе (порт 8099, свежая sqlite, офлайн)
cd apps/api
DATABASE_URL="sqlite:////<SCRATCH>/f3-synthetic-baseline.db" \
IMAGE_PROVIDER=real VERIFIER_PROVIDER=real RAG_PROVIDER=mock \
CV_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/packages/cv/data \
CV_EVAL_REPORT_PATH=/Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye/qa/scan-eval-runs/case-20260917-synthetic-baseline/eval_report_snapshot.json \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
./.venv/bin/uvicorn app.main:app --port 8099 &
until curl -s http://127.0.0.1:8099/v1/healthz | grep -q '"warm":true'; do sleep 2; done
cd ..

# 3. Прогон двумя батчами ~991 фото (ради времени одного вызова — см. отчёт)
#    разбиение файлов на batchA/batchB — симлинки, чётные/нечётные по алфавиту
qa/.venv/bin/python qa/scan_eval.py --mode rich --api-url http://127.0.0.1:8099 \
    --split all --photos-dir <SCRATCH>/case-synth-batchA --out-dir <SCRATCH>/scan-eval-batchA
qa/.venv/bin/python qa/scan_eval.py --mode rich --api-url http://127.0.0.1:8099 \
    --split all --photos-dir <SCRATCH>/case-synth-batchB --out-dir <SCRATCH>/scan-eval-batchB

# 4. Объединение + разрезы + запись сюда + снимок для CV_EVAL_REPORT_PATH
qa/.venv/bin/python qa/analyze_case_synthetic_baseline.py

# 5. Проверка живьём
curl -s http://127.0.0.1:8099/v1/metrics/scan

# 6. Погасить
kill <PID_uvicorn>
```

## Итог одной строкой

match-rate (гейт confident/not_in_catalog, официальный) 6,8%; **raw top-1 (matches[0],
без гейта) 69,4%** — расхождение это калибровка порога `not_in_catalog` по `gap`
(contracts v0.4.5), не качество ранжирования ANN. Подробности, разрезы по near-dup/
fallback-детектору и интерпретация — `reports/f3-synthetic-baseline.md`.
