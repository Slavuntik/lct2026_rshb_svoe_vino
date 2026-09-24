# devops: стенд + чтение этикетки VLM 27B через GPU-шлюз (env, без кода/тега)

## Вход
Тимлид включил на стенде `CV_FUSION_TEXT_SOURCE=vlm` в `/opt/somelye/somelye.env`
(`VISION_LLM_URL`/`KEY` заданы впервые, бэкап `.bak-before-vlm`); `CV_FUSION_MERGE_MODEL_TEXT=1`
не менялся (с hack-v10). Проверил сам: `healthz`→`warm:true`, `cv_index_version` не менялся
(`case-20260921-d1-b384`), сервис перезапущен 14:20:47 МСК (позже hack-v12 13:57:51). Кода и
тега нет — не деплоил.

## 100 фото и точность
`real_photos_serve.py --flat-only` на стенде (порт 80, прод-путь) → 100/100, 0 ошибок HTTP,
5м27с → scp в `served/stand-hack-v12-vlm.jsonl`. `qa/real_photos_eval.py --served
stand-hack-v12{,-vlm}.jsonl` (62 sure+likely): **96.8% (60/62)** против hack-v12 95.2%
(59/62) — ровно ожидание брифа. Половины A/B: 100.0%/**93.5%** (было 100.0%/90.3%).
Diff всех 100 фото: 16 расхождений `flat_slug`; внутри измеряемых 62 — **1 улучшение, 0
регрессий**: `94.55_02-09-2026_16-53-08.webp` `denisov-winery-pino-nuar-...-125` →
`denisov_pazori_risling` (истина). Остальные 15 — вне размеченного множества (NONE/не
sure-likely), на метрику не влияют, но видно, что новый источник текста меняет
предсказания широко, не точечно.
Тайминги `flat_ms` (`numpy.percentile`, метод `qa/scan_eval.py:_percentile`): p50/p95/max =
**3264/3567/4528 мс** — НИЖЕ hack-v12 (4227/4955/5949). 0 фото дороже лимита 10 с; 0 фото
дороже дедлайна модели `VISION_LLM_TIMEOUT_S=6.5` с — даже max уложился. Ускорение при
добавлении сетевого вызова к GPU не объяснено причинно (риск 1).

## Источник текста правда vlm? (rich-режим, 5 фото)
По коду: `_fusion_text_and_vectors` (`service.py:463-519`) считает `text_source`
(vlm/vlm_local/vlm_both/ocr), но `routers/scan.py:273-290` собирает
`ScanPhotoRichResponse` БЕЗ этого поля, и `cv/archive.py:74-91` тоже не пишет его в
сайдкар архива сканов — поле живёт только внутри процесса, наружу не выходит нигде.
Эмпирически (5 фото, `/v1/scan/photo`): поля ответа — `flat_ms, flat_slug, gap, matches,
not_in_catalog, ocr_verified, photo, rich_ms, slug, timing_ms, top1_score` — источника
нет. 5 новых записей в `/opt/somelye/data/scans/{bucket}/20260922/*.json` — тот же набор
(+id/ts/bucket/reason/shown_slug/index_version/orig_bytes/image_saved/verified_slug/
размеры) — тоже нет. Журнал `somelye-api` за окно прогона — 0 строк `vision_llm:` (эти
warning — только на сбой/фолбэк, `vision_llm.py:110,113`; успех не логируется).
**Источник нигде не виден** — ни в rich-ответе, ни в архиве, ни в логах на успехе; есть
только косвенные признаки (+1.6 п.п. точности, 0 warning-фолбэков, более быстрые тайминги).

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` — строка/JSON
«hack-v12 + VLM 27B (env)» добавлены и закоммичены. Заливка на стенд **не выполнена** —
по заданию делает тимлид.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v12{,-vlm}.jsonl
```

## Риски / предложения
1. Тайминги упали (4227→3264 мс p50) при добавлении сетевого вызова к GPU — причина не
   доказана (возможно RapidOCR 640+960 на CPU был узким местом), переизмерить отдельно,
   бокс шумный; выборка 62 фото небольшая, 1 смена = 1.6 п.п. (повтор наблюдения v9-v12).
2. `contracts/openapi.yaml` в рабочей копии — чужие незакоммиченные 187 строк (похоже на
   architect, `reports/architect-submission-audit.md`), не трогал, в коммит не включал.
3. Предложения: вывести `text_source` хотя бы в архив сканов (`cv/archive.py`) — решение
   architect/ml-lead; закрепить в TEAM.md исключение для devops по `qa/scan-eval-runs/`
   (повтор v9-v12 п.1, не закрыто).
