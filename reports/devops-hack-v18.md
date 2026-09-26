# devops: выкат hack-v18 — первый выкат после влития 123 коммитов Михаила

## Выкат
`git status` чист (чужие незакоммиченные правки product в `apps/web/public/legal/*`/`ru.ts` не
трогал) → `git push origin main` (5 коммитов, `d13236d..bed50ea`) → тег `hack-v18` на `bed50ea`,
push тега **19:01:16 МСК**. `ExecMainStartTimestamp` 23.09 00:21:18 → **26.09 19:03:35 МСК**,
`healthz warm:true` к **19:04:03** (~28с). Индексы/`packages/cv` не менялись — README/PDF/
докс-врезки/openapi box/фронт-гейт витрины/флейк-фикс verify, qa-auto approve (`qa-auto-post-merge.md`).

## Проверка шлюза ДО прогона (п.2)
3× `POST /chat/completions` model=qwen3.8-27b max_tokens=5 (`vlm-lab/.env`, не печатал): **3/3
успешны, 0.78/0.88/0.76с** — в этот момент шлюз здоров, вопреки утренним ~20с-зависаниям у тимлида.

## Дым (п.3), всё 200/0 ошибок
healthz 145мс; metrics/scan 146мс (f1 0.968/0.984); `GET /v1/wines/denisov_rubin_klaret_krasnaya_
strelka` 158мс — similar_wines 6/6 с именами; `pairing/dish{Мясо и стейки}` 215мс → BBQ 6/6 красное;
`chat{wine_id}` 14.73с SSE — citation №1 ровно запрошенное вино; `eval/predict` 1 фото 5.9с,
`{"slug":...}`; `GET /` 200/2500Б; `GET /v1/shelf/health` **404 JSON** → `probeShelfHealth()` не
ok → «Витрина» скрыта кодом (`AppShell.tsx`), проверено ответом сервера, не браузером.

## Тишина: 100 фото (п.4) — 19:09:27→19:17:42 МСК, 8м15с
`cpulab/real_photos_serve.py --flat-only --api http://127.0.0.1:8000` → `stand-hack-v18.jsonl`
(100/100, 0 HTTP-ошибок) → scp в `served/`. `flat_ms` p50/p95/max **4650/6416/6537мс** (min 3463) —
**0 фото быстрее 2с** (0 похожих на локальный фолбэк) — шлюз держался весь прогон. `real_photos_
eval.py --served`: **top-1 96.8% (60/62)**, половины A/B 100.0%/93.5% — БЕЗ изменений от hack-v16/17.
Diff vs `stand-hack-v16.jsonl` (100): 2 расхождения, ОБА вне измеряемых 62 (пара кадров «Усадьба
Дивноморское», смена между двумя винами линейки). Diff vs `stand-hack-v17-20.jsonl` (20/100
пересечение): 0. Вывод: код safe, шлюз в это окно здоров → **обычная запись, не деградация**.

## Разовый бенчмарк verify() на 4 vCPU (п.5)
`pytest` в `/opt/somelye/venv` не было (`--no-dev`) — поставил `uv pip install --python .../venv/bin/
python pytest`, прогнал, удалил тем же `uv pip uninstall`; `somelye-api` не перезапускался, healthz
warm:true до/после. Env как в `somelye.env` (иначе замер нечестный): `OMP_NUM_THREADS=3 PADDLE_PDX_
ENABLE_MKLDNN_BYDEFAULT=0 FLAGS_use_mkldnn=0 CV_OCR_ENGINE=rapid RUN_CV_BENCHMARKS=1 pytest
tests/test_verify.py -k p95_latency`. Результат (n=30): **p50 1993 / p95 2118 / max 2240мс**. Тест
красный против контрактных 700мс — ожидаемо, не регрессия: обязательный обход бага oneDNN на этом
боксе (`infra/ams3/README.md`, «~2.5-3с вместо 0.5с» — фактически чуть лучше). Честная цифра для
жюри: verify() на ams3 ≈2.0с p50, не 456мс с Mac разработки.

## Снимок метрик (п.6)
`.../real-photos-stand/README.md` — новая строка+раздел hack-v18. `eval_report_snapshot.json`:
**`f1_top1`/`f1_top5` НЕ трогал** (0.968/0.984, по слову тимлида — здоровый снимок hack-v16/17, код
скана не менялся); обновил `latency_ms` + `eval_set`/`measured_at`. **Заливка на стенд
`/opt/somelye/data/eval_report_snapshot.json` ЗАБЛОКИРОВАНА** классификатором авто-режима («Production
Deploy») — не обходил; `/v1/metrics/scan` отдаёт корректные f1, но старую `eval_set`/`latency_ms` (см. риски).

## Как воспроизвести
```
ssh -i ~/.ssh/ci_do_ams3 -o ControlPath=/tmp/somelye-%r@%h somelye@89.110.72.101 \
  'systemctl show -p ExecMainStartTimestamp somelye-api; curl -s http://127.0.0.1:8000/v1/healthz'
cd packages/cv && .venv/bin/python ../../qa/real_photos_eval.py --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v{16,18}.jsonl
```

## Риски / предложения
1. **Нужно действие Вячеслава/тимлида**: залить `eval_report_snapshot.json` на стенд — scp-команда в
   отчёте выше, заблокирована авто-режимом у меня; либо разрешить Bash-правило для этого пути.
2. Полного `stand-hack-v17.jsonl` нет (только 20-сэмпл, 0 diff) — сверил и с последним полным v16
   (2 diff вне измеряемых 62, линейка «Усадьба Дивноморское», не блокер); `pytest` по-прежнему не
   в `/opt/somelye/venv` штатно — следующий бенчмарк verify() снова потребует temp install/uninstall.
