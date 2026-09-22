# devops: выкат hack-v10 — гастропары, слияние OCR+модель, алиас винодельни

## Выкат
`git push origin main` (15 коммитов, `914207c..984027e`) → тег `hack-v10` на `984027e` → push тега →
GitHub Actions «Deploy hack stand (ams3)» подхватил push тега сам (guard→tests→deploy). Подтверждение
НЕ по файлам: `ExecMainStartTimestamp` 04:55:53→06:28:53 МСК (позже пуша/тега), monotonic
96879755779→102459550662, `healthz`→`warm:true`, `cv_index_version` не изменился (только код).

## Данные и конфиг стенда
1. `infra/ams3/sync-data.sh`, шаг «6/7 кейс» — теперь копирует и `winery_aliases.json`; выполнен
   разовый rsync ОДНОГО файла → `/opt/somelye/data/case/winery_aliases.json`, md5 совпал
   (`0fb87cf1...`).
2. `CV_FUSION_MERGE_MODEL_TEXT=1` в `infra/ams3/somelye.env.example` и
   `infra/local-check/run-check-server.sh`; на стенде дописан в `/opt/somelye/somelye.env` (бэкап
   `.bak-before-hack-v10`, diff — 1 строка). На стенде без эффекта (VLM не настроена) — подтверждено
   таймингами ниже (разница в пределах шума).

## 100 фото и точность
`cpulab/real_photos_serve.py --api http://127.0.0.1 --src /opt/somelye/cpulab/real-photos
--flat-only --timeout 30` (nginx :80, как прод-трафик) → 100/100, 0 HTTP-ошибок, 7м04с → scp в
`case-data/real-photos-labels/served/stand-hack-v10.jsonl`. `qa/real_photos_eval.py --served
stand-hack-v{8,9,10}.jsonl` (62/100 sure+likely): hack-v10
**95.2% (59/62)** vs hack-v9 93.5% (58/62) — +1 фото. Diff по всем 100 фото — РОВНО одна смена:
93.97 `pomeste-golubitskoe-shardone-rezerv-...` → `golubitskoe-estate-chardonnay` (алиас
винодельни сработал), 0 регрессий — совпадает с офлайн-приёмкой ML-1 (`reports/ml-eng-ml1.md`).
Тайминги (`flat_ms`, `numpy.percentile`, метод `qa/scan_eval.py:_percentile`): p50/p95/max =
**4186/4798/5812 мс** (hack-v9: 4204/5096/6585) — чуть быстрее, в пределах шума VPN-бокса; 0 фото
дороже 10 с.

## Дым (contracts/post-scan.md v1.0)
`GET /v1/wines/{id}/pairings`, гость-Bearer:
- RAG-каталог (`chateau-de-talu-...-145`, null-sensory 500 из `reports/qa-auto-post-scan.md`) →
  **200** `basis=unavailable`, `message` непуст — 500 пофикшен.
- Каталог кейса, вне RAG (`abrau-dyurso-abrau-kupazh-svetlyy-...-125`) → **200** `basis=heuristic`, 3 тега.
- Несуществующий слаг → **404** `not_found`.
Веб: бандл `index-GsbRLEOA.js` → `index-Ckg3tKSJ.js` (CSS-хеш тот же), подтверждено локально
(`127.0.0.1:8000`) и публично (`curl http://89.110.72.101/` → 200, тот же новый хеш).

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` — строка hack-v10,
залиты на стенд (бэкап `.bak-before-hack-v10`). `http://89.110.72.101/v1/metrics/scan` →
`f1_top1=0.952` — совпадает.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{8,9,10}.jsonl
```

## Риски / предложения
1. (повтор `devops-hack-v9.md` п.2, не закрыто) Снимок `qa/scan-eval-runs/` формально в зоне
   ml-lead — предлагаю architect/pm закрепить исключение для devops в таблице зон явно.
2. `git status` перед коммитом показал 2 незакоммиченных файла чужой зоны
   (`packages/cv/cv/shelf_crop.py`, `tests/test_shelf_crop.py`, WIP ml-lead/ml-engineer, `9ed4c66`)
   — не трогал; в тег не попали (`git archive HEAD`).
3. Порт `:80` общий с публичным трафиком (прогон ~7 мин, конкурентных сканов не заметил, отдельно
   не изолировал); `CV_FUSION_MERGE_MODEL_TEXT=1` на стенде декоративен без VLM — выигрыш
   (95.2%→96.8% офлайн, ML-1) достанется с секретами `VISION_LLM_URL`/`KEY` (`BOARD.md`).
