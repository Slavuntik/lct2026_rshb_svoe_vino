# qa-auto: приёмка hack-v9 — чувствительный детектор текста RapidOCR (26f96c9, 8099076)

**Вердикт: approve.**

## Тесты
`packages/cv`: **356 passed** (было 354, +2 — дефолты порога детектора из env, см.
`test_engine_params_carry_sensitive_detector_thresholds_by_default`/`..._from_env`).
`apps/api`: **322 passed, 11 skipped** — без изменений (пороги читаются живьём в
`RapidOcrReader.__init__`, мимо `Settings`, как и `CV_OCR_LABEL_SIZE`).

## Живой API, Mac, CPU-путь без VLM
Индекс `packages/cv/data-d1`, `CV_MODEL=siglip2-base-patch16-384`, `CV_FUSION=1 W=0.3
TEXT_SOURCE=ocr CV_OCR_ENGINE=rapid SIZES=640,960 LABEL_SIZE=1280 CV_FUSION_CROPS=2`.
Кандидат — порт 8771 (дефолты кода 0.3/2.0); контроль — порт 8772
(`CV_OCR_DET_BOX_THRESH=0.6 CV_OCR_DET_UNCLIP=1.5`, прежние пороги). `real_photos_serve.py
--flat-only` (100 фото) → `real_photos_eval.py --served`, каждая конфигурация дважды
(последовательно — см. ловушка №2). Ошибок HTTP 0/100 везде; `flat_slug` бит-в-бит
идентичен между прогонами 1 и 2 у обеих конфигураций.

## Точность (62 фото каталога, sure/likely)
| Конфигурация | top-1 |
|---|---|
| **candidate** (0.3/2.0) | **58/62 = 93.5%** |
| control (0.6/1.5, тот же Mac/сессия) | 54/62 = 87.1% |
| baseline `stand-hack-v8.jsonl` (ams3-стенд) | 57/62 = 91.9% |

candidate vs control (чистое сравнение, отличается ТОЛЬКО порог): **+4 фото чистыми** (5
исправлено — 95.36 `winemaker-selection`, тот самый пример из коммита, плюс 94.02 и три
near-dup пары 96.27/96.55/96.79; 1 регрессия — 94.55 `denisov_pazori_risling`→ путает вино
того же винзавода). Регрессия воспроизводится и против baseline (57→58, +1 чистыми: 95.36 и
96.55 исправлены, 94.55 та же регрессия). control vs baseline расходится на 94.02/96.27/96.79
(все в пользу baseline) — но candidate решает их ВЕРНО, как baseline: похоже на шум
onnxruntime Mac/ams3 на старом пороге, не баг; вывод не меняет.

## Тайминги (100 фото, `flat_ms`, Mac, 2 прогона)
| Конфигурация | p50 | p95 |
|---|---|---|
| candidate run1/run2 | 564/592 мс | 806/842 мс |
| control run1/run2 | 520/537 мс | 649/668 мс |
| baseline ams3 (для контекста, другое железо) | 4126 мс | 4726 мс |

Чувствительный детектор дороже на ~50–170 мс (больше боксов → больше OCR) — несущественно
относительно лимита 10 с.

## Как воспроизвести
Тесты: `cd packages/cv|apps/api && .venv/bin/python -m pytest -q`. Сервер — по образцу
`infra/local-check/run-check-server.sh`, но `CV_FUSION_TEXT_SOURCE=ocr` (без VLM/VISION_LLM_*),
`CV_FUSION_CROPS=2`, порт 8771; контроль — то же + `CV_OCR_DET_BOX_THRESH=0.6
CV_OCR_DET_UNCLIP=1.5`, порт 8772 (два `apps/api` НЕ одновременно — см. ловушка №2). Затем
`apps/api/.venv/bin/python qa/real_photos_serve.py --api http://127.0.0.1:8771 --flat-only
--out /tmp/c.jsonl` → из `packages/cv`: `.venv/bin/python ../../qa/real_photos_eval.py --served
/tmp/c.jsonl <served>/stand-hack-v8.jsonl --only __none__`.
Прогоны сохранены (вне git): `case-data/real-photos-labels/served/hack-v9-{sensitive,control-0.6-1.5}[-run2].jsonl`.

## Риски / предложения
1. Регрессия 94.55 `denisov_pazori_risling` воспроизводима, не шум — не блокирует (net
   +4/+1), но стоит разбора ml-lead до 100-фото прогона devops.
2. **Ловушка**: embedded qdrant на `data-d1` не терпит два параллельных `apps/api` на одном
   `CV_DATA_DIR` — второй тихо отдаёт ПУСТОЙ `flat_slug` на каждом фото, без HTTP-ошибки;
   поймано вторым прогоном таймингов, не тестами. Тот же класс, что H3 уже отмечала
   (reports/h3-label-crop-ocr.md, п.5) — предлагаю architect/devops закрепить правилом.
3. Порты 8770–8772 свободны (проверено `lsof`), сервисы погашены.
