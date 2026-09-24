# devops: выкат hack-v17 — similar_wines/top_styles_named, правки после обхода жюри

## Выкат
`git status` чист → `git push origin main` (5 коммитов `342b915..2b60ea5`: `e06c132` фронт-правки,
`f9f043d` openapi 0.3.6, `db090e1` backend similar_wines/top_styles_named, `b18c662` фронт-рендер,
`2b60ea5` qa-auto approve) → тег `hack-v17` на `2b60ea5`, push тега 00:18:25 МСК → GitHub Actions
подхватил сам. `ExecMainStartTimestamp` 23:08:01→**00:21:18 МСК**, `healthz warm:true` к 00:21:44
(~26с, обычный старт). `index_version`/`rag_index_version`/`cv_index_version` не менялись
(`20260922.1`/`20260922.1`/`case-20260921-d1-b384`) — индексы и env этой волной не трогали, как
и задано.

## Дым (гостевой токен `POST /v1/auth/guest` 201/268мс)
- `GET /v1/wines/{id}` × 3 (`denisov_rubin_klaret_krasnaya_strelka`, `a-gordienko-m-nikolaev-pino-nuar-…`,
  `abrau-dyurso-abrau-durso-brut-rose-reserve-…`) — `similar_wines` 6/6 с непустыми именами на каждом,
  150/229/152мс.
- `GET /v1/taste/profile` гостя → 403 `consent_required` (контрактное поведение — профиль требует
  регистрации, не 500), 144мс. Доп. проверка: свежий зарегистрированный аккаунт → 200, `top_styles_named: []`
  в ответе, 144мс (регистрация 201/288мс; заметка: email на `.invalid` TLD отклонён валидатором, `.com` прошёл).
- `POST /v1/pairing/dish` («Мясо и стейки»→BBQ) → 200, 6/6 красное, 219мс.
- `POST /v1/chat` с `wine_id=denisov_rubin_klaret_krasnaya_strelka` → citation №1 ровно про это вино
  (текст ответа явно его цитирует), 8832мс.
- `GET /` (веб) → 200, 2500 байт, 138мс. Сырых слагов не искал по вёрстке (SPA рендерит клиентски) —
  проверено через API: `similar_wines`/`top_styles_named` несут имена, не голые слаги (пункты выше).

## Контрольный прогон 20/100 фото (`--flat-only`, вне git → `served/stand-hack-v17-20.jsonl`)
0 ошибок HTTP. Diff с теми же 20 фото в `stand-hack-v16.jsonl`: **0 расхождений** `flat_slug` —
ожидание 1:1 подтверждено. `flat_ms` p50/p95/max = **4356/5103/5359 мс** (n=20), 0 фото дороже 10 с.

## Снимок метрик
Цифры НЕ переизмерялись — строка `hack-v17` в `qa/scan-eval-runs/real-photos-stand/README.md`
(таблица + раздел) помечена «фронт и поля карточки, скан не менялся, цифры от hack-v16», плюс
результат контрольных 20 фото рядом. `eval_report_snapshot.json` не трогал (задание — только README);
заливка на стенд не делалась (вне задания).

## Как воспроизвести
```
ssh -i ~/.ssh/ci_do_ams3 -o ControlPath=/tmp/somelye-%r@%h somelye@89.110.72.101 \
  'systemctl show -p ExecMainStartTimestamp somelye-api; curl -s http://127.0.0.1:8000/v1/healthz'
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{16,17-20}.jsonl
```

## Риски / предложения
1. Одна SSH-команда в середине 20-фото прогона зависла ~7.5 мин (вместо долей секунды) —
   вероятная причина: всплеск ~6 быстрых SSH/SCP-подключений за 4с в неудачной первой попытке
   (перепутан порт: default `--api` скрипта `real_photos_serve.py` — `127.0.0.1:8765`, боевой
   uvicorn слушает `:8000`, `somelye-api.service`), похоже на fail2ban. На данные не повлияло
   (0 ошибок, чистый diff), но подтверждает правило ORCHESTRATION.md про один канал ControlMaster
   без очередей быстрых новых подключений — стоит явно указывать `--api http://127.0.0.1:8000`
   в будущих прогонах на сервере, брифы/README можно дополнить этим портом.
2. Регистрация с email на зоне `.invalid` даёт 422 (валидатор pydantic EmailStr отклоняет TLD) —
   не блокер (нашёл случайно на тестовом аккаунте), но если где-то в тестовых фикстурах используется
   `.invalid`/подобные зоны — стоит знать про это ограничение.
3. Откат — тег `hack-v16` (`b492bbe`) тем же путём (`DEPLOY_REF=b492bbe push-release.sh` или ручной
   `git push --force` тега не требуется, откатывать выкатом на старый коммит).
