# qa-auto: генеральная репетиция официального скрипта — стенд hack-v10

## Прогон
Официальный `case-data/eval/participant_test.sh` (без изменений, как будет на созвоне) →
`--endpoint http://89.110.72.101/v1/eval/predict`, манифест `rehearsal_manifest.tsv` (готов
заранее, 100 фото, прошёл собственную валидацию скрипта офлайн перед запуском). **Единственный
запуск против стенда**, без повторов и параллели (скрипт и так последовательный — один запрос
ждёт ответа перед следующим): 2026-09-22 07:17:38 → 07:22:41 МСК (5 мин 03 с), 100/100 строк,
exit code 0. Выход (вне git): `case-data/real-photos-labels/served/rehearsal-stand-hack-v10.jsonl`.

## Top-1 (62 фото каталога, разметка part1+part2, confidence sure/likely)
**59/62 = 95.2%** — совпадает с принятым числом hack-v10 (`reports/devops-hack-v10.md`,
`qa/scan-eval-runs/real-photos-stand/README.md`). Промахи (3):
- `91.43_02-09-2026_12-08-50.webp`: true=`merlo-litavshhuk` (likely) → pred=`kuban-vino-vysokiy-bereg-merlo-krasnoe-suhoe-135`
- `94.55_02-09-2026_16-53-08.webp`: true=`denisov_pazori_risling` (sure) → pred=`denisov-winery-pino-nuar-shardone-ekstra-bryut-beloe-125`
- `95.76_23-08-2026_18-19-43.webp`: true=`denisov_pereplyas_kurmyshi` (sure) → pred=`denisov_barynya_kurmyshi`

## latency_ms скрипта (100 фото, curl `time_total`, метод — как «Опорные цифры» TEAM.md)
p50/p95/max = **2916 / 3993 / 4823 мс**; дороже 10 000 мс — **0**. Перцентиль — линейная
интерполяция (`qa/scan_eval.py:_percentile`, тем же методом считал devops). Ниже, чем `flat_ms`
devops на hack-v10 (4186/4798/5812) — тот замер сделан на самом стенде (nginx :80,
сервер-сторона), этот — с клиента через публичный интернет `participant_test.sh`/curl (полный
round-trip, включая аплоад фото); сеть в моменте была быстрее серверного замера. Оба варианта
далеко от лимита 10 с.

## Пустые ответы
**0/100** (`predicted_slug=null` не встретился ни разу; в 62-наборе тоже 0).

## Сравнение с devops `served/stand-hack-v10.jsonl` (тот же стенд, снят иначе — `photo/flat_slug/flat_ms` через 127.0.0.1)
**100/100 фото — слаг совпал 1:1** (`predicted_slug` репетиции == `flat_slug` devops
по каждому файлу), расхождений **0**. Сопоставление по имени файла (`image_path` ↔ `photo`).

## Как воспроизвести
```
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines
bash case-data/eval/participant_test.sh --images-dir case-data/real-photos \
  --manifest case-data/real-photos-labels/rehearsal_manifest.tsv \
  --endpoint http://89.110.72.101/v1/eval/predict \
  --output case-data/real-photos-labels/served/<новый-файл>.jsonl   # старый output скрипт не перезапишет
```
Подсчёт (top-1 по 62/sure-likely, latency, пустые, diff с devops): разметка — `part1.csv` +
`part2.csv` (`true_slug not in ("", "NONE")`, `confidence in (sure, likely)`); percentile —
линейная интерполяция как `qa/scan_eval.py:_percentile`.

## Риски / предложения
1. Задание — ровно один запуск против стенда; второй не делал (не грузить стенд перед
   созвоном организаторов). Если нужен повторный прогон — новое имя `--output`, тот же манифест.
2. 100/100 совпадение с независимым прогоном devops (другой клиент, другой протокол доступа —
   127.0.0.1 против публичного адреса) — подтверждает детерминированность ответа стенда на
   одинаковых байтах изображения, кэш/гонок между прогонами не видно.
3. Латency на созвоне организаторов будет зависеть от их сети — наш прогон подтверждает только
   что путь «клиент → публичный `89.110.72.101` → ответ» укладывается в лимит с большим
   запасом (max 4823 мс против лимита 10 000 мс), не гарантирует те же цифры с другой точки
   выхода в интернет.
