# devops: контрольный прогон перед сдачей — hack-v22, GPU свободен

## Шлюз до старта (п.1)
3× `POST /chat/completions` model=`qwen3.8-27b` max_tokens=5, с Mac напрямую на шлюз, до какого-либо
обращения к стенду: **3/3 успешны, HTTP 200, 1153/1063/980 мс** (27.09 03:15:54 МСК). Здоров — прогон
не отменялся.

## Стенд
hack-v22 уже стоял (тег на `c432c09`, тимлид выкатил вручную `push-release.sh` — Actions блокирован
биллингом). Индексы/env без изменений с hack-v19: `cv_index_version=case-20260921-d1-b384`,
`index_version`/`rag_index_version=20260922.1`, `ExecMainStartTimestamp` 02:07:45 МСК, `healthz
warm:true`. Скрипт `real_photos_serve.py` на сервере md5-идентичен репозиторию.

## Flat-прогон, путь скрипта жюри (п.2), тишина 03:16:11→03:23:43 МСК (7м32с)
`--flat-only` → `stand-hack-v22.jsonl`, 100/100, 0 HTTP-ошибок. `flat_ms` p50/p95/max **4436/5216/
6455 мс** (n=100, min 3525) — **0 дороже 10с, 0 быстрее 2с** (модель участвовала на каждом фото).
`qa/real_photos_eval.py --served`: **top-1 96.8% (60/62)**, половины A/B 100.0%/93.5% — БЕЗ ИЗМЕНЕНИЙ
от hack-v18, те же 2 известных промаха (Литавщук, Курмыши). Diff vs `stand-hack-v18.jsonl` (100
фото): **2 расхождения, ОБА вне измеряемых 62** (честный NONE, пара «Усадьба Дивноморское» качнулась
между винами линейки) — 0 регрессий.

## Rich-прогон (п.3), тишина 03:24:58→03:36:04 МСК (11м6с) — честный f1_top5
Без `--flat-only` → `stand-hack-v22-rich.jsonl`, 100/100, 0 ошибок. Flat-нога совпала со всеми 100
фото независимого flat-прогона (0 расхождений — детерминизм подтверждён). **top-5 98.4% (61/62)** —
**впервые с hack-v13 не перенос, а честный замер на текущем коде**, ровно ожидание задания. `rich_ms`
p50/p95/max **3187/3488/3862 мс** (n=100), 0 дороже 10с. Честно: flat-нога САМОГО rich-прогона дала
1 выброс 10186 мс (фото `bukovinka`, ответ верный) — вероятная CPU-конкуренция двух
последовательных запросов в rich-режиме на 4-vCPU боксе; в независимом flat-прогоне это же фото было
в общем диапазоне (max 6455) — канонические цифры не затронуты.

## Снимок метрик (п.4)
`qa/scan-eval-runs/real-photos-stand/{eval_report_snapshot.json,README.md}` — строка/раздел hack-v22.
`f1_top1=0.968`, `f1_top5=0.984` — оба честно измерены на здоровом шлюзе сейчас, не подгонялись
(совпали с ожиданием точь-в-точь). `latency_ms`/`rich_latency_ms`/`eval_set`/`measured_at` обновлены.
Заливка на стенд НЕ выполнена — по заданию, отдельно сделает тимлид.

## Коммит/push (п.5)
`git commit -- qa/scan-eval-runs/real-photos-stand/eval_report_snapshot.json
qa/scan-eval-runs/real-photos-stand/README.md reports/devops-final-check.md` → `git push origin
main`. Тег не ставил — код не менялся, `hack-v22` уже существует и уже на стенде.

## Как воспроизвести
```
ssh -i ~/.ssh/ci_do_ams3 -o ControlPath=/tmp/somelye-%r@%h somelye@89.110.72.101 \
  'systemctl show -p ExecMainStartTimestamp somelye-api; curl -s http://127.0.0.1:8000/v1/healthz'
cd packages/cv && .venv/bin/python ../../qa/real_photos_eval.py --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{18,22}.jsonl
cd packages/cv && .venv/bin/python ../../qa/real_photos_eval.py --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v22-rich.jsonl
```

## Риски / предложения
1. Побочный выброс 10186 мс на flat-ноге rich-прогона (см. выше) — не блокер, но если когда-нибудь
   потребуется гонять flat и rich ОДНОВРЕМЕННО (не последовательно, как здесь), стоит перепроверить.
2. `f1_top5` теперь свежий и честный — следующая волна, трогающая `packages/cv`, обязана переизмерить
   его rich-прогоном заново, а не переносить это значение по умолчанию.
