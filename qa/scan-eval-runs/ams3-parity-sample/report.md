# Отчёт scan_eval — кейс-сканер ЛЦТ

Сгенерировано: 2026-09-21T08:42:20.945628+00:00 · режим: `rich` · API: `http://89.110.72.101`
Сплит: `holdout` (seed=1337, holdout_frac=0.1) · каталог: `/private/tmp/claude-501/-Users-vyacheslavfokin-ClaudeWorkspace/3ef8e524-2f02-493d-bf79-36104e8b36c9/scratchpad/case-synth-honest` · фото загружено: 180

## Метрики

| Метрика | Значение |
|---|---|
| match-rate (top-1) | 0.4444 (44.4%) |
| match-rate (top-5) | 0.8833 (88.3%) |
| F1 top-1 (macro) | 0.4444 |
| F1 top-5 (macro) | 0.7772 |
| p50 времени ответа | 2277 мс |
| p95 времени ответа | 2974 мс |
| среднее время ответа | 2122 мс |
| n (успешных ответов) | 180 из 180 |
| error-rate (нет валидного ответа) | 0.0% |
| средний top1_score (rich) | 0.8871 |
| средний gap (rich) | 0.0288 |

Критерии кейса (case.md): match-rate top-1 90-100%, SLA p95 ≤3000 мс. Сейчас: match-rate=44.4%, p95=2974 мс (в рамках SLA).

## Топ промахов top-1 (диагностика near-duplicates, case.md)

| true_slug | предсказано | раз |
|---|---|---|
| alma-valley-tempranilo-rezerv-krasnoe-suhoe-14 | alma-valley-merlo-rezerv-krasnoe-suhoe-14 | 1 |
| khrustaleva-76-muscat-bryut-beloe | khrustaleva-76-muscat-polusladkoe-beloe | 1 |
| kuban-vino-shato-tamane-terruar-krasnostop-saperavi-2022-krasnoe-suhoe-125 | vinodelnya-zhakov-saperavi-vyderzhannoe-krasnoe-suhoe-115 | 1 |
| novyj-svet-kaberne | novyy-svet-dom-shampanskih-vin-rossiyskoe-shampanskoe-vyderzhannoe-bryut-rozovoe-novyy-svet-kaberne-kaberne-sovinon-11 | 1 |
| shato-taman-grape-dance | shato-taman-grape-dance-1 | 1 |
| soyuz-vino-sheremetevskie-pogreba-krasnoe-polusladkoe-krasnye-sorta-vinograda-11 | soyuz-vino-sheremetevskie-pogreba-shardone-beloe-suhoe-11 | 1 |

Интерпретация F1 — macro-F1 по slug'ам, реально встретившимся как true_slug в этом eval-сете (см. `scan_eval.py::_macro_f1`); ТЗ кейса формулу не даёт, только цель «виден отрыв лидера от конкурентов» — сверить с реальным скриптом оценки по приезду (qa/mock_case_script.sh — рехёрсал их описанного поведения, не их код).
