# devops: выкат hack-v11 — подсказка «одна бутылка крупно» + обвязка CV_SHELF_CROP (выключена)

## Выкат
`git status` чист → `git push origin main` (9 коммитов, `d423bdb..c3288e9`) → тег `hack-v11` на
`c3288e9` → push тега → GitHub Actions «Deploy hack stand (ams3)» подхватил сам (guard→tests→deploy;
деплой-джоба требует `tests: success`, значит CI зелёный). Подтверждение НЕ по файлам:
`ExecMainStartTimestamp` 06:28:53→**08:27:19 МСК**, monotonic 102459550662→109565771526, `healthz`→
`warm:true`, `index_version`/`rag_index_version`/`cv_index_version` (`case-20260921-d1-b384`) не
изменились — только код.

## Флаг CV_SHELF_CROP (env стенда — не менял)
Проверено до и после выката: строки `CV_SHELF_CROP` в `/opt/somelye/somelye.env` нет (не `=1`);
код-дефолт `false` (`apps/api/app/config.py:322`). Обвязка ML-2/ML-3 (8459f57, 21667bc) на стенде
физически не активна — поведение CV-пути не изменилось (подтверждено ниже diff'ом 0/100).

## 100 фото на стенде и точность
`cpulab/real_photos_serve.py --api http://127.0.0.1 --src /opt/somelye/cpulab/real-photos
--flat-only --timeout 30` (nginx :80, прод-трафик) → 100/100, 0 HTTP-ошибок, 7м15с → scp в
`case-data/real-photos-labels/served/stand-hack-v11.jsonl`. `qa/real_photos_eval.py --served
stand-hack-v{9,10,11}.jsonl` (62/100 sure+likely): hack-v11 **95.2% (59/62)** = hack-v10 95.2%
(59/62) — 0 изменений. Построчный diff ВСЕХ 100 фото hack-v10→hack-v11 (`flat_slug` по каждому
photo): **0 расхождений**, ответы 1:1 — ожидаемо (CV/OCR-путь не тронут), проверено явно, не по
агрегату.

**Тайминги** (`flat_ms`, `numpy.percentile`, метод `qa/scan_eval.py:_percentile`): hack-v11
p50/p95/max = **4276/4977/6631 мс**; hack-v10 тем же методом 4186/4798/5812 — чуть медленнее, в
пределах шума VPN-бокса (аналогично сдвигу hack-v8→hack-v9); 0 фото дороже лимита 10 с.

## Дым веба
`GET http://89.110.72.101/` → **200**. Бандл `/assets/index-Ckg3tKSJ.js` → `index-Vas0FSuS.js`
(CSS `index-CvQZS2nn.css`) — обновился, соответствует правке `ScanScreen.tsx`/`ru.ts` (c3288e9,
подпись сканера + подсказка на 5 кандидатах).

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` — строка hack-v11
добавлена, залито на стенд (`/opt/somelye/data/eval_report_snapshot.json`, бэкап
`.bak-before-hack-v11`). `http://89.110.72.101/v1/metrics/scan` → `f1_top1=0.952`, `eval_set`
упоминает hack-v11 — проверено curl после заливки.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{9,10,11}.jsonl
```

## Риски / предложения
1. (повтор v9/v10 п.2, не закрыто) Снимок `qa/scan-eval-runs/` формально в зоне ml-lead —
   architect/pm закрепить исключение для devops в таблице зон TEAM.md.
2. `git status` перед выкатом был чист впервые за три волны (v9/v10 — были чужие незакоммиченные
   WIP-файлы) — отклонений от протокола в этот раз нет.
3. Порт `:80` общий с публичным трафиком (прогон ~7 мин, load average 0.50 до старта, конкурентных
   сканов не заметил, отдельно не изолировал — повтор наблюдения v9/v10).
4. `CV_SHELF_CROP` на стенде выключен ОТСУТСТВИЕМ строки в `somelye.env`, не явным `=0` — задача
   прямо запретила трогать env стенда, поэтому не правил; если/когда флаг решат когда-нибудь
   включать, стоит сначала явно прописать `=0` в примере/env, чтобы отсутствие строки не читалось
   как «не настроено».
