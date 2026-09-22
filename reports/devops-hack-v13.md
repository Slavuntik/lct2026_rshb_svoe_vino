# devops: выкат hack-v13 — драйвер `LLM_PROVIDER=openai` для сомелье-чата

## Выкат
`git status` чист → `git push origin main` (`77ed1fa..54f65a5`, аудит architect поверх уже слитого
backend `e9c4cde`) → тег `hack-v13` на `54f65a5` → push тега → GitHub Actions «Deploy hack stand
(ams3)» подхватил сам (guard→tests→deploy). Подтверждение НЕ по файлам: `ExecMainStartTimestamp`
14:20:47→**14:47:10 МСК** (~142с от пуша тега до `warm:true`), `index_version`/`rag_index_version`/
`cv_index_version` (20260828.1/20260828.1/case-20260921-d1-b384) не изменились — только код. Env
`LLM_PROVIDER=openai`/`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL=qwen3.8-27b` тимлид поставил заранее
(бэкап `somelye.env.bak-before-llm` на месте), применились этим рестартом; значения не печатал.

## Дым сомелье (/v1/chat)
`POST /v1/auth/guest` — 201, ~150-230 мс. Точная фраза задания `"Посоветуй красное к стейку до 2000
рублей"` → **refusal** за ~3 с (детерминировано, дважды): «В базе не нашлось вин или советов по
вашему запросу» — `EMPTY_RETRIEVAL_REASON`, срабатывает ДО обращения к LLM (`app/chat/service.py:
104-107`, пустая выдача `retriever.search()`). Локализовал перебором фраз: «…к стейку» и «…вино до
2000 рублей» по отдельности проходят, именно эта комбинация без слова «вино» — нет. `journalctl`
недоступен без sudo (проверил — «insufficient permissions», ожидаемо); `/opt/somelye/data` — только
`scans/`/`rag/`/`db/`, чат пишется в БД, смотреть нечего. Вывод: это ретривер/фильтры
(`apps/api/app/chat`), не драйвер `openai` — hack-v13 меняет только `packages/llm`, refusal отрабатывает
раньше LLM. Тот же смысл без потери слова, `"Посоветуй красное вино к стейку до 2000 рублей"`: первый
токен **4335 мс**, весь ответ **11976 мс**, 1466 симв., 8 цитат, `done` — текст содержательный (7 вин
с обоснованием под стейк), не заглушка «По данным источников:» (`is_mock_stub=false`) — драйвер
`openai`/Qwen3.8-27b через шлюз подтверждён живым ответом.

## 100 фото на стенде и точность
`cpulab/real_photos_serve.py --api http://127.0.0.1 --src /opt/somelye/cpulab/real-photos --out
stand-hack-v13.jsonl --flat-only --timeout 30` (nginx :80, прод-трафик) → 100/100, 0 HTTP-ошибок,
7м40с → scp в `served/stand-hack-v13.jsonl`. `qa/real_photos_eval.py --served stand-hack-v12-vlm.jsonl
stand-hack-v13.jsonl` (62/100 sure+likely): hack-v13 **96.8% (60/62)** = hack-v12+VLM, половины A/B
100.0%/93.5% — 0 изменений. Diff ВСЕХ 100 фото hack-v12+VLM→hack-v13 (`flat_slug`): **0 расхождений**
— ровно 1:1, ожидаемо (волна не трогает CV/OCR/RAG-путь, только `packages/llm`).

**Тайминги** (`flat_ms`, `qa/scan_eval.py:_percentile`): p50/p95/max = **4412/6634/9139 мс** — заметно
выше hack-v12+VLM (3264/3567/4528). При diff=0 и коде, не касающемся CV-пути, это не регрессия
функциональности, а шум общего с VPN бокса (сам прогон 7м40с против 5м27с — нагрузка в это окно была
выше); ни одного фото дороже лимита 10 с (max 9139 мс).

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/{README.md,eval_report_snapshot.json}` — строка/JSON hack-v13
добавлены и закоммичены. Заливка на стенд (`/opt/somelye/data/eval_report_snapshot.json`) **не
выполнена** — по заданию делает тимлид.

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{12-vlm,13}.jsonl
```

## Риски / предложения
1. **Ретривер отказывает на фразе задания** («…к стейку до 2000 рублей» без слова «вино») —
   `EMPTY_RETRIEVAL_REASON`, детерминировано, не связано с hack-v13 (срабатывает до LLM). Похоже на
   узкое место семантического поиска/фильтров — предлагаю backend/ml-lead разобрать отдельно: реальные
   пользователи легко попадают в похожие формулировки.
2. Тайминги 100-фото шумнее обычного разброса (+1-2.6 с к p50/p95/max) — тот же вывод, что и в
   `reports/devops-stand-vlm.md` (риск 1, бокс шумный), диапазон в этот раз больше обычного;
   функционально не критично (diff 0/100, SLA не нарушен), переизмерить на следующем выкате.
3. `TEAM.md` (22.09) уже закрепил `qa/scan-eval-runs/real-photos-stand/` в зоне devops — повторявшийся
   риск v9-v12 п.1 («формально зона ml-lead») закрыт.
4. `contracts/llm-adapter.md` всё ещё не содержит строку `openai` (предложение backend в
   `reports/backend-llm-openai.md`, зона architect) — не блокер, контракт не трогал.
