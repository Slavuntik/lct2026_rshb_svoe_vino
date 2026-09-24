# Отчёт scan_eval — кейс-сканер ЛЦТ

Сгенерировано: 2026-09-17T19:16:07.792185+00:00 · режим: `rich` · API: `http://127.0.0.1:8000`
Сплит: `all` (seed=1337, holdout_frac=0.2) · каталог: `/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-honest` · фото загружено: 2054

## Метрики

| Метрика | Значение |
|---|---|
| match-rate (top-1) | 0.4017 (40.2%) |
| match-rate (top-5) | 0.8306 (83.1%) |
| F1 top-1 (macro) | 0.3997 |
| F1 top-5 (macro) | 0.3557 |
| p50 времени ответа | 355 мс |
| p95 времени ответа | 635 мс |
| среднее время ответа | 343 мс |
| n (успешных ответов) | 2054 из 2054 |
| error-rate (нет валидного ответа) | 0.0% |
| средний top1_score (rich) | 0.8901 |
| средний gap (rich) | 0.0279 |

Критерии кейса (case.md): match-rate top-1 90-100%, SLA p95 ≤3000 мс. Сейчас: match-rate=40.2%, p95=635 мс (в рамках SLA).

## Топ промахов top-1 (диагностика near-duplicates, case.md)

| true_slug | предсказано | раз |
|---|---|---|
| alma-valley-tempranilo-rezerv-krasnoe-suhoe-14 | alma-valley-merlo-rezerv-krasnoe-suhoe-14 | 1 |
| aya-organic-wine-vineyards-purity-in-syrah-sira-rozovoe-suhoe-132 | aya-organic-wine-vineyards-purity-in-syrah-sira-krasnoe-suhoe-146 | 1 |
| belbek-belbek-muskat-beloe-suhoe-128 | belbek-muskat-muskat-belyy-beloe-suhoe-127 | 1 |
| belbek-sandzhoveze-krasnoe-suhoe-14 | belbek-belbek-sandzhoveze-krasnoe-suhoe-138 | 1 |
| bogovich-wine-vineyard-kaberne-sovinon-krasnoe-suhoe-125 | soyuz-vino-soyuz-vino-kaberne-sovinon-krasnoe-suhoe-12 | 1 |
| cabernet-franc-pinot-noir-2022 | vibes-cabernet-franc-pinot-noir-pino-nuar-krasnoe-suhoe-125 | 1 |
| chateau-cachalot-pino-nuar-krasnoe-suhoe-12 | chateau-cachalot-risling-beloe-suhoe-115 | 1 |
| chateau-de-talu-blan-semilon-beloe-suhoe-127 | agrolayn-lame-du-vin-saperavi-krasnoe-suhoe-13 | 1 |
| denisov-winery-rkatsiteli-beloe-suhoe-112 | denisov-winery-risling-beloe-suhoe-115 | 1 |
| denisov_rubin_klaret_krasnaya_strelka | denisov_pino_noir_klaret | 1 |

Интерпретация F1 — macro-F1 по slug'ам, реально встретившимся как true_slug в этом eval-сете (см. `scan_eval.py::_macro_f1`); ТЗ кейса формулу не даёт, только цель «виден отрыв лидера от конкурентов» — сверить с реальным скриптом оценки по приезду (qa/mock_case_script.sh — рехёрсал их описанного поведения, не их код).
