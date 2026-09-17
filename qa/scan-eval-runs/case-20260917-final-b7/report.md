# Отчёт scan_eval — кейс-сканер ЛЦТ

Сгенерировано: 2026-09-17T04:18:27.421175+00:00 · режим: `rich` · API: `http://127.0.0.1:8000`
Сплит: `all` (seed=1337, holdout_frac=0.2) · каталог: `/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-batchA + /private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-batchB (батчи A+B, синтетика seed=20260917)` · фото загружено: 1982

## Метрики

| Метрика | Значение |
|---|---|
| match-rate (top-1) | 0.4157 (41.6%) |
| match-rate (top-5) | 0.8985 (89.8%) |
| F1 top-1 (macro) | 0.4103 |
| F1 top-5 (macro) | 0.4083 |
| p50 времени ответа | 329 мс |
| p95 времени ответа | 588 мс |
| среднее время ответа | 297 мс |
| n (успешных ответов) | 1980 из 1982 |
| error-rate (нет валидного ответа) | 0.1% |
| средний top1_score (rich) | 0.8706 |
| средний gap (rich) | 0.0284 |

Критерии кейса (case.md): match-rate top-1 90-100%, SLA p95 ≤3000 мс. Сейчас: match-rate=41.6%, p95=588 мс (в рамках SLA).

## Предупреждения

- 1 из 991 запросов не дали валидный ответ (см. records[].error)
- 1 из 991 запросов не дали валидный ответ (см. records[].error)
- ПОЛНОМАСШТАБНЫЙ СИНТЕТИЧЕСКИЙ baseline на ЭТИХ ЖЕ эталонах (self-match-style, seed=20260917) — НЕ полевые фото. Полевой замер — по приезду публичного датасета (qa/acceptance.md §9).
- Прогнан двумя батчами (A/B, по ~991 фото) ради времени одного вызова, объединено этим скриптом — метрики те же функции, что scan_eval.py.

## Топ промахов top-1 (диагностика near-duplicates, case.md)

| true_slug | предсказано | раз |
|---|---|---|
| abrau-dyurso-abrau-durso-reserve-brut-shardone-beloe-bryut-115 | abrau-dyurso-abrau-durso-brut-rose-reserve-pino-nuar-beloe-bryut-12 | 1 |
| abrau-dyurso-brut-dor-riesling-risling-beloe-bryut-12 | abrau-dyurso-brut-dor-blanc-de-noirs-pino-nuar-beloe-bryut-125 | 1 |
| abrau-dyurso-imperatorskoe-bryut-shardone-beloe-12 | abrau-dyurso-imperatorskoe-polusuhoe-shardone-beloe-12 | 1 |
| abrau-dyurso-udelnoe-vedomstvo-imperatorskoe-beloe-polusladkoe | abrau-dyurso-imperatorskoe-polusladkoe-shardone-beloe-12 | 1 |
| abrau-dyurso-victor-dravigny-koshernoe-bryut-shardone-beloe-125 | abrau-dyurso-victor-dravigny-brut-shardone-beloe-bryut-12 | 1 |
| alma-valley-solntse-vozduh-vinograd-merlo-krasnoe-polusladkoe-14 | alma-valley-solntse-vozduh-vinograd-sira-shiraz-rozovoe-polusladkoe-12 | 1 |
| aratti-vremena-goda-saperavi-merlo-vesna | aratti-vremena-goda-saperavi-leto | 1 |
| aratti-vremena-goda-saperavi-vyderzhannyj-zima | aratti-vremena-goda-saperavi-leto | 1 |
| b-yu-rne-sh-ardone-suhoe-beloe | vinodelnya-byurne-vione-beloe-suhoe-135 | 1 |
| belmas-winery-malbec-katya-belmas-malbek-rozovoe-suhoe-11 | belmas-winery-malbec-katya-malbek-rozovoe-suhoe-129 | 1 |

Интерпретация F1 — macro-F1 по slug'ам, реально встретившимся как true_slug в этом eval-сете (см. `scan_eval.py::_macro_f1`); ТЗ кейса формулу не даёт, только цель «виден отрыв лидера от конкурентов» — сверить с реальным скриптом оценки по приезду (qa/mock_case_script.sh — рехёрсал их описанного поведения, не их код).
