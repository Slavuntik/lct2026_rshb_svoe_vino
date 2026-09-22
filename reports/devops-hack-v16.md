# devops: выкат hack-v16 — «Что подать» по фото блюда, `wine_id` в чате, RAG-индекс 2103 вина

## Выкат — оба шага без отката
`git status` чист → `git push origin main` (19 коммитов ролей, `7a71351..b492bbe`) → тег `hack-v16` на
`b492bbe` → push тега → GitHub Actions подхватил сам. Код-рестарт: `ExecMainStartTimestamp` **22:36:11
МСК**, `healthz warm:true` за ~31с (502 первые ~26с — обычный старт); `rag_index_version`/
`cv_index_version` этим рестартом не менялись (данные CI не доставляет).

RAG-индекс (2103 вина вместо 1978, backend `a686bf4`/`8cd0355`): blue-green без простоя (встроенный
Qdrant однопроцессный — новое НЕ поверх боевого). `rsync --exclude .lock case-data/rag-index-20260922/`
(сверена `diff -rq` байт-в-байт с закоммиченным `packages/rag/data`) → **новый** каталог
`/opt/somelye/data/rag-20260922/`; на сервере ДО переключения подтверждено: `labels.jsonl` **2103
строки**, `manifest.json` version `20260922.1`, wines 2103/wineries 138/knowledge 5592. Бэкап
`/opt/somelye/somelye.env.bak-before-hack-v16`, `sed -i` заменил только `RAG_DATA_DIR` (diff имён
переменных до/после — пусто) → `sudo systemctl restart somelye-api`. Второй рестарт **22:37:22 МСК**,
`healthz warm:true` за ~7с, `rag_index_version` **20260828.1→20260922.1** подтверждён; кэш каталога для
подбора к блюду (`768e410`) тоже греется в этом старте. Старый `/opt/somelye/data/rag/` (1978 вин) НЕ
удалён — путь отката.

## Дым (строго ДО прогона фото)
Гостевой токен 201/168мс. `POST /v1/pairing/dish` 3 тега: «Мясо и стейки»→BBQ 6/6 красное/251мс;
«Рыба»→Блюда из рыбы 6/6 белое/187мс; «Выпечка и десерты» 5 сладкое+1 полусладкое/170мс — все цвета/
сахар ожидаемые. `POST /v1/pairing/dish-photo` с фото бутылки (`real-photos/1.73_...webp`) →
`status=bottle`, не `food`, `timing_ms=2969`/4.59с сетевых. `POST /v1/chat` с `wine_id` близнеца
Союз-Вино (`soyuz-vino-kubanskoe-traditsionnoe-beloe-suhoe-07-...`, вариант 0.7Л) → citation №1 ровно
про запрошенное вино, 8.85с. 2 вопроса жюри («...к стейку до 2000 рублей», «Какое красное подать к
стейку?») — **0 отказов**, честное «нет данных о ценах» на первом, 7.68/7.32с.

## Тишина (100 фото)
`--flat-only` на стенде, окно 22:40:48→22:48:12 МСК (7м24с), 100/100, 0 ошибок HTTP →
`stand-hack-v16.jsonl`. `qa/real_photos_eval.py --served`: top-1 **96.8% (60/62)**, A/B 100.0%/93.5% —
БЕЗ изменений от hack-v15. Diff всех 100 фото: **0 расхождений** `flat_slug` (байт-в-байт) — сильнее
ожидания задания (расхождения только вне 62), ожидаемо: ни один из 8 коммитов волны не трогает
`packages/cv`. `flat_ms` p50/p95/max = **4348/5203/6568 мс** (n=100), 0 фото дороже 10 с.

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` — строка/JSON hack-v16
добавлены (`measured_at` 22:48:12 МСК, `f1_top5`/`rich_latency_ms` — перенос из hack-v13, не
переизмерялись). Заливка на стенд НЕ выполнена — по заданию этой волны.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{15,16}.jsonl
```
Откат индекса: `RAG_DATA_DIR=/opt/somelye/data/rag` в `somelye.env` + `sudo systemctl restart
somelye-api` (или восстановить `.bak-before-hack-v16` целиком).

## Риски / предложения
1. Ни один шаг 2–4 не сломался — откат не потребовался.
2. `sync-data.sh` всё ещё без `RAG_INDEX_DST` (аналог `CV_INDEX_DST`) — второй раз делаю blue-green
   индекса вручную (`rsync`+`sed`+restart); предлагаю формализовать скриптом, раз практика повторилась.
3. `.bak-before-hack-v16` остаётся на сервере (как все `.bak-*` этого проекта, не чистятся).
4. Живой хаос-тест GPU-шлюза (открыто с hack-v15) — вне задания этой волны, не трогал.
5. `packages/rag/data` в git добавляет ~90 МБ к каждому будущему `git archive`/CI-чекауту — не блокер,
   но со временем стоит взвесить (архитектор/тимлид уже приняли это решение сознательно).
