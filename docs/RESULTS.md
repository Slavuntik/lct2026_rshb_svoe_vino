# Результаты экспериментов

Сгенерировано `python -m winescan.eval.report` 2026-09-15 16:21. Не редактировать руками.
Определения метрик — `src/winescan/eval/metrics.py`; подвыборки — docs/DATA.md, раздел 6.

## Прогоны

Дата — время прогона: код детектора и слияния менялся в течение дня, сверяйте с docs/WORKLOG.md.

| прогон | дата | кроп | OCR | top-1 | top-5 | top-1 похожие (pHash) | top-1 общий эталон | F1@1 | детекция, мс | OCR, мс |
|---|---|---|---|---|---|---|---|---|---|---|
| `public__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.5,0.5__detector__ocr` | 09-15 16:20 | detector | да | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 457 | 299 |
| `synth_v1__siglip2-base-patch16-224__gt` | 09-15 15:05 | gt | нет | 0.659 | 0.907 | 0.553 | 0.407 | 0.660 | 0 | 0 |
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.3,0.7__gt` | 09-15 15:35 | gt | нет | 0.905 | 0.989 | 0.865 | 0.424 | 0.905 | 0 | 0 |
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.5,0.5__detector__ocr` | 09-15 16:13 | detector | да | 0.819 | 0.911 | 0.789 | 0.492 | 0.819 | 207 | 128 |
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.5,0.5__gt` | 09-15 15:21 | gt | нет | 0.904 | 0.990 | 0.866 | 0.542 | 0.904 | 0 | 0 |
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.7,0.3__gt` | 09-15 15:35 | gt | нет | 0.893 | 0.991 | 0.852 | 0.441 | 0.893 | 0 | 0 |
| `synth_v1__siglip2-so400m-patch14-384__detector` | 09-15 15:20 | detector | нет | 0.732 | 0.857 | 0.692 | 0.390 | 0.733 | 242 | 0 |
| `synth_v1__siglip2-so400m-patch14-384__gt` | 09-15 15:07 | gt | нет | 0.853 | 0.987 | 0.796 | 0.424 | 0.857 | 0 | 0 |
| `synth_v1__siglip2-so400m-patch14-384__gt__ocr` | 09-15 15:17 | gt | да | 0.853 | 0.987 | 0.796 | 0.424 | 0.857 | 0 | 159 |
| `synth_v1__siglip2-so400m-patch14-384__none` | 09-15 15:12 | none | нет | 0.254 | 0.476 | 0.225 | 0.169 | 0.254 | 0 | 0 |

## Переранжирование (лучший вес по top-1 на том же прогоне)

| прогон | сигнал | вес | top-1 | top-5 | top-1 похожие | top-1 общий эталон |
|---|---|---|---|---|---|---|
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.5,0.5__detector__ocr` | SIFT top-5 | 0.2 | 0.863 | 0.911 | 0.827 | 0.492 |
| `synth_v1__siglip2-so400m-patch14-384+siglip2-so400m-patch14-384__label@0.5,0.5__gt` | SIFT top-5 | 0.8 | 0.945 | 0.990 | 0.893 | 0.441 |
| `synth_v1__siglip2-so400m-patch14-384__gt` | SIFT top-5 | 0.8 | 0.937 | 0.987 | 0.885 | 0.458 |
| `synth_v1__siglip2-so400m-patch14-384__gt__ocr` | текст OCR | 0.01 | 0.856 | 0.987 | 0.802 | 0.492 |

Синтетика оптимистична (в кадре пиксели эталона), см. ARCHITECTURE.md, раздел 4.

## Публичные фото через сервис

`participant_test.sh` кейсодержателя и `/v1/scan` с настройками по умолчанию (2026-09-15 16:00). Разметка неофициальная (`configs/eval_public_labels.csv`).

| фото | ожидается | /v1/eval/predict | верно | задержка скрипта, мс | /v1/scan | визуальный скор | в сервисе, мс |
|---|---|---|---|---|---|---|---|
| 019c68d0.jpg | (нет в каталоге) | vaynkraft-pino-nuar-krasnoe-suhoe-13 | вне каталога: да | 1769 | not_found | 0.6599 | 1054.9 |
| 02eef911.webp | massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16 | massandra-muskatel-belyy-belye-sorta-vinograda-beloe-sladkoe-16 | да | 1791 | found | 0.7842 | 1189.0 |
| 096ca74e.jpg | (нет в каталоге) | abrau-dyurso-imperatorskoe-bryut-shardone-beloe-12 | вне каталога: да | 2024 | not_found | 0.6951 | 1196.3 |
