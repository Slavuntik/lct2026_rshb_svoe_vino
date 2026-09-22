# qa-auto: F1 top-5 (rich-прогон) — стенд hack-v13

## Задача
ТЗ требует F1 топ-1/топ-5 в API (`case.md` строка 21); `/v1/metrics/scan` отдавал `f1_top5: null`
(`reports/architect-submission-audit.md`). Закрыто rich-прогоном 100 фото на стенде.

## Ожидание готовности стенда
Три критерия тимлида проверялись по SSH (мультиплекс `ControlMaster`, чтобы не словить fail2ban):
процесс `real_photos_serve` (`pgrep -fa "real_photos_serv[e]"`), `healthz warm:true`,
`ExecMainStartTimestamp` > 14:30 МСК. Все три выполнились в 14:47:10 (рестарт hack-v13), но
«процесса нет» ложно-положительно и ДО старта flat-прогона devops — досмотрел отдельным окном
~12 мин и поймал его вживую: старт 14:51:31, конец ~14:59:04 (100/100, `stand-hack-v13.jsonl`),
только после этого запустил свой прогон (иначе оба 100-фото прогона конкурировали бы за 4 vCPU).

## Rich-прогон
Скрипт на сервере (`/opt/somelye/cpulab/real_photos_serve.py`) md5-идентичен репозиторию —
апдейт не потребовался. `python3 real_photos_serve.py --api http://127.0.0.1 --src
/opt/somelye/cpulab/real-photos --out out/stand-hack-v13-rich.jsonl --timeout 30` (БЕЗ
`--flat-only`) → **100/100, 0 HTTP-ошибок, 10м55с** → scp в
`case-data/real-photos-labels/served/stand-hack-v13-rich.jsonl` (вне git).

## Top-1 / top-5 (62 фото, sure+likely; `qa/real_photos_eval.py --served`)
**top-1 60/62 = 96.8%** (0 расхождений с независимым flat-only `stand-hack-v13.jsonl` devops на
всех 100 фото — детерминизм подтверждён). **top-5 61/62 = 98.4%** → `f1_top5 = 0.984` (закрытое
множество: F1 = доля истины в top-5). 1 фото восстановлено топ-5 (`95.76_23-08-2026_18-19-43.webp`,
`denisov_pereplyas_kurmyshi`, top-1 спутал с `denisov_barynya_kurmyshi`, истина на позиции 2/5);
1 фото истина вне топ-5 (`91.43_...webp`, `merlo-litavshhuk` — известный текстовый пробел, BOARD.md).

## Тайминги (numpy.percentile, `qa/scan_eval.py:_percentile`)
`flat_ms` (нога `/v1/eval/predict` внутри rich-прогона) p50/p95/max = 3316/3638/4395 мс — ниже
независимого flat-only devops (4412/6634/9139) в то же окно, бокс шумный (ожидаемо, см. риски
devops). `rich_ms` (`/v1/scan/photo`) p50/p95/max = **3075/3501/14580 мс** — max 1 выброс на
`41.8_22-08-2026_20-56-40.webp` (not_in_catalog=true), не на графиковом (10 с — лимит только
flat-эндпоинта). 0 ошибок.

## Снимок метрик
`qa/scan-eval-runs/real-photos-stand/eval_report_snapshot.json`: `f1_top5: 0.984`,
`rich_latency_ms` добавлен (вне контракта `ScanMetricsResponse`, информационно, как уже был
`latency_ms`), `measured_at` обновлён на момент rich-замера. `README.md`: колонка top-5, строка
hack-v13, новый раздел с разбором. Заливка на стенд НЕ делалась (по заданию — тимлид отдельно).

## Как воспроизвести
```
cd packages/cv && CASE_DATA_DIR=/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data \
  .venv/bin/python ../../qa/real_photos_eval.py --ocr crop320 --only zzz \
  --served ../../../case-data/real-photos-labels/served/stand-hack-v13-rich.jsonl \
           ../../../case-data/real-photos-labels/served/stand-hack-v12-vlm.jsonl
```

## Риски / предложения
1. `rich_ms` p95/max заметно выше `flat_ms` (лишний POST + card/candidates/analogs) — не влияет
   на официальный скрипт (только flat-эндпоинт таймится), но если UI когда-нибудь начнёт зависеть
   от p95 rich-пути, 14.58 с разово — заметный выброс, стоит присмотреть на следующих волнах.
2. `f1_top1`/`match_rate`/`raw_top1_rate_ungated` в снимке не трогал (совпали 1:1 с уже
   закоммiченными devops числами) — редактировал только `f1_top5`, `eval_set`, `rich_latency_ms`,
   `measured_at`.
3. Зона `qa/scan-eval-runs/real-photos-stand/` по TEAM.md закреплена за devops — правил её по
   прямому пункту брифа тимлида (см. задачу); если это разовое исключение, а не постоянный
   сдвиг зоны — стоит явно решить на будущее (следующий выкат снова devops или снова qa-auto).
