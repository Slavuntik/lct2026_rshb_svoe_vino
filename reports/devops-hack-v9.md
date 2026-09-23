# devops: выкат hack-v9 — чувствительный детектор текста RapidOCR (26f96c9, 8099076)

## Выкат
`git push origin main` (9 коммитов, `20d7bb0..5c10806`) → тег `hack-v9` на `5c10806` → push тега →
GitHub Actions «Deploy hack stand (ams3)» подхватил push тега сам (guard→tests→deploy). Подтверждение
НЕ по файлам: `ExecMainStartTimestampMonotonic` вырос 91973066486→96879755779 (рестарт 04:55:53 МСК,
позже пуша 04:53:44 и тега), `healthz` → `warm:true`, `cv_index_version` не изменился
(`case-20260921-d1-b384` — только код, индекс тот же).

## Env стенда
`CV_OCR_DET_BOX_THRESH`/`CV_OCR_DET_UNCLIP` в `/opt/somelye/somelye.env` отсутствуют — правка не
нужна, действуют новые дефолты кода (0.3/2.0). Попутно: `VISION_LLM_URL`/`KEY` на стенде не заданы →
`CV_FUSION_TEXT_SOURCE=vlm` фактически идёт через RapidOCR-фолбэк, пороги детектора реально влияют
на прод.

## 100 фото на стенде и метрики
`cpulab/real_photos_serve.py --api http://127.0.0.1 --flat-only --timeout 30` (через nginx :80, как
прод-трафик) → 100/100, 0 HTTP-ошибок → scp в `case-data/real-photos-labels/served/stand-hack-v9.jsonl`.

**Точность** (`qa/real_photos_eval.py --served stand-hack-v8.jsonl stand-hack-v9.jsonl`, 62/100 sure+likely):
hack-v9 **93.5% (58/62)** vs hack-v8 91.9% (57/62) — **+1 фото чистыми**, совпадает по знаку и
величине с офлайн-приёмкой qa-auto (net +1 против baseline на Mac).

**Тайминги** (`flat_ms`, `numpy.percentile` линейная интерполяция — метод `qa/scan_eval.py:_percentile`):
hack-v9 p50/p95/max = **4204/5096/6585 мс**; hack-v8 тем же методом 4126/4726/5718 (README ранее
писал p95 4791 — расхождение метода округления, не данных; p50/max совпали). 0 фото дороже 10 с в
обеих версиях; +78/+370/+867 мс — больше боксов у чувствительного детектора → больше OCR-проходов.

Снимок `qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` обновлён и залит
на стенд (`/opt/somelye/data/eval_report_snapshot.json`, бэкап `.bak-before-hack-v9` рядом). Проверено
`http://89.110.72.101/v1/metrics/scan` → отдаёт `f1_top1=0.935`.

## Отклонение от плана (п.1 — git status)
Перед выкатом `git status` НЕ был чист: `qa/real_photos_cpu_path.py` — незакоммиченная правка чужой
зоны (фильтр `part3-field.csv` из выборки метрики, похоже на WIP qa-auto/ml-lead). Не трогал
(read-only чужая зона по ORCHESTRATION.md); не блокирует — `git push`/тег берут закоммиченное дерево
(`git archive HEAD` в `push-release.sh`), рабочая копия не участвует. Проверил отдельно: сам
`real_photos_eval.py`, который я запускал, той же дырой не страдает — `in_cat` строится через
`p in idx_of` (`photos.json`), а `part3-field.csv` (18 строк на момент выката, 0 пересечений имён с
эталонными 100) в 62-подсчёт не просочился — проверено явно.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{8,9}.jsonl
```
Тайминги: `numpy.percentile([...flat_ms], 50|95)` + `max()` по тому же jsonl.

## Риски / предложения
1. Регрессия `denisov_pazori_risling` (qa-auto report, п.1) воспроизвелась и на стенде (57→58, не
   59) — ml-lead уже в курсе по приёмке, не блокирует.
2. Обновление `qa/scan-eval-runs/real-photos-stand/` формально в зоне ml-lead по таблице TEAM.md, но
   и по протоколу TEAM.md п.3, и по своему брифу devops обязан писать этот снимок на каждом выкате —
   предлагаю architect/pm явно закрепить это исключение в таблице зон TEAM.md, чтобы не спорить заново.
3. Порт :80 стенда — общий с публичным трафиком; на время прогона (~8 мин) конкурентных сканов не
   заметил (load average 0.6 до старта), но отдельно не изолировал.
