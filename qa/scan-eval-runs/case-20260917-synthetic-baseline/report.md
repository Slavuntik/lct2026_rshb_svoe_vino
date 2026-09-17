# Отчёт scan_eval — кейс-сканер ЛЦТ

Сгенерировано: 2026-09-17T00:36:11.399325+00:00 · режим: `rich` · API: `http://127.0.0.1:8099`
Сплит: `all` (seed=1337, holdout_frac=0.2) · каталог: `/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-batchA + /private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-batchB (батчи A+B, синтетика seed=20260917)` · фото загружено: 1982

## Метрики

| Метрика | Значение |
|---|---|
| match-rate (top-1) | 0.0681 (6.8%) |
| match-rate (top-5) | 0.8986 (89.9%) |
| F1 top-1 (macro) | 0.0604 |
| F1 top-5 (macro) | 0.4086 |
| p50 времени ответа | 59 мс |
| p95 времени ответа | 366 мс |
| среднее время ответа | 95 мс |
| n (успешных ответов) | 1982 из 1982 |
| error-rate (нет валидного ответа) | 0.0% |
| средний top1_score (rich) | 0.8706 |
| средний gap (rich) | 0.0597 |

Критерии кейса (case.md): match-rate top-1 90-100%, SLA p95 ≤3000 мс. Сейчас: match-rate=6.8%, p95=366 мс (в рамках SLA).

## Предупреждения

- ПОЛНОМАСШТАБНЫЙ СИНТЕТИЧЕСКИЙ baseline на ЭТИХ ЖЕ эталонах (self-match-style, seed=20260917) — НЕ полевые фото. Полевой замер — по приезду публичного датасета (qa/acceptance.md §9).
- Прогнан двумя батчами (A/B, по ~991 фото) ради времени одного вызова, объединено этим скриптом — метрики те же функции, что scan_eval.py.

## Топ промахов top-1 (диагностика near-duplicates, case.md)

| true_slug | предсказано | раз |
|---|---|---|
| a-gordienko-m-nikolaev-sira-nuvo-krasnoe-suhoe-115 | vinodelnya-zhakov-tavkveri-escape-krasnoe-suhoe-117 | 1 |
| abrau-dyurso-imperatorskoe-bryut-shardone-beloe-12 | abrau-dyurso-imperatorskoe-polusuhoe-shardone-beloe-12 | 1 |
| abrau-dyurso-pino-nuar-krasnoe-suhoe-12 | abrau-dyurso-shardone-beloe-suhoe-13 | 1 |
| abrau-dyurso-udelnoe-vedomstvo-imperatorskoe-beloe-bryut | abrau-dyurso-imperatorskoe-polusladkoe-shardone-beloe-12 | 1 |
| abrau-dyurso-udelnoe-vedomstvo-imperatorskoe-beloe-polusladkoe | abrau-dyurso-imperatorskoe-polusladkoe-shardone-beloe-12 | 1 |
| abrau-dyurso-victor-dravigny-koshernoe-bryut-shardone-beloe-125 | abrau-dyurso-victor-dravigny-brut-shardone-beloe-bryut-12 | 1 |
| agrolayn-mountain-eagle-chardonnay-shardone-beloe-suhoe-12 | agrolayn-mountain-eagle-white-blend-vione-beloe-suhoe-115 | 1 |
| agrolayn-mountain-eagle-muscat-muskat-beloe-suhoe-125 | agrolayn-mountain-eagle-white-blend-vione-beloe-suhoe-115 | 1 |
| agrolayn-mountain-eagle-pedro-ximenez-pedro-himenes-beloe-suhoe-11 | agrolayn-mountain-eagle-white-blend-vione-beloe-suhoe-115 | 1 |
| agrolayn-mountain-eagle-semillon-semilon-beloe-suhoe-11 | agrolayn-mountain-eagle-viognier-vione-beloe-suhoe-115 | 1 |

Интерпретация F1 — macro-F1 по slug'ам, реально встретившимся как true_slug в этом eval-сете (см. `scan_eval.py::_macro_f1`); ТЗ кейса формулу не даёт, только цель «виден отрыв лидера от конкурентов» — сверить с реальным скриптом оценки по приезду (qa/mock_case_script.sh — рехёрсал их описанного поведения, не их код).
