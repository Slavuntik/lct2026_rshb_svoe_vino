# backend — text_source/label_text: архив + INFO-лог (22.09)

Задача тимлида: `PhotoScanResult` уже несёт `text_source`/`label_text` (`app/cv/
service.py`, CV_FUSION), но `routers/scan.py` и `cv/archive.py` их не писали — источник
текста этикетки (vlm/vlm_local/vlm_both/ocr) нигде не виден снаружи процесса
(`reports/devops-stand-vlm.md`).

## Что сделано

1. `app/cv/archive.py::archive_scan()` — сайдкар `<id>.json` несёт `text_source`/
   `label_text`, прокинуты из `PhotoScanResult` как есть (`None` вне CV_FUSION).
   Персональных данных нет — архив временный, поле несёт только текст этикетки вина.
2. `app/routers/scan.py::scan_photo` (только rich-ветка) — INFO-лог на успешное чтение
   (`result.text_source is not None`): `scan_photo: текст этикетки прочитан source=%s
   len=%d ms=%d` — источник, длина `label_text`, `timing_ms` всего пайплайна (отдельного
   таймера шага чтения в `PhotoScanResult` нет — заводить поле значит править
   `service.py` вне зоны backend без брифа ML-лида, см. риски). Без содержимого текста,
   без ключей шлюза. flat и `/v1/eval/predict` (общий `flat_scan_response`) не логируют
   — тот же принцип приватности приватной выборки кейсодержателя, что у архива (v0.4.10).
   В rich-ответ поле НЕ добавлено (контракт не меняется).
3. **Находка при реализации**: боевой запуск (`infra/ams3/somelye-api.service`,
   `infra/Dockerfile.api` — везде голый `uvicorn app.main:app`, без `--log-level`) не
   настраивает root-логгер; по Python-дефолту он на WARNING без хендлеров, и
   `logging.lastResort` тоже ловит только WARNING+. Голый `logger.info(...)` в такой
   конфигурации не долетел бы никуда — проверено эмпирически тем же `uvicorn.config.
   LOGGING_CONFIG`, что использует установленный в проекте uvicorn (dictConfig без ключа
   "root"). Добавил хендлер и `setLevel(INFO)` ТОЛЬКО на логгер `app.routers.scan` — не
   на root, другие модули (`vision_llm.py` и т.п.) не затронуты.

## Тесты

`test_scan_archive.py` (+2): сайдкар несёт `text_source`/`label_text`, включая `None`
вне CV_FUSION. `test_scan_photo_text_source_log.py` (новый, 5): `source=ocr` (фолбэк) и
`source=vlm_local` (смокнутая `vision_llm.read_label`) — оба в логе, без содержимого
текста; лог молчит при CV_FUSION=0, на `?flat=1` и на `/v1/eval/predict`.

```
cd apps/api && .venv/bin/pytest -q
```
**372 passed, 11 skipped** (было 365/11 до правки — `reports/backend-swagger.md`, тот же
день; skip — integration без `RUN_*_INTEGRATION`). Регрессий нет.

## Предложение к контракту

`contracts/image-scan.md` v0.4.10 перечисляет состав сайдкара `<id>.json` без
`text_source`/`label_text` — предлагаю architect дописать оба поля в список: решением
21.09 уже разрешён сбор диагностики в архиве, персональных данных в новых полях нет.

## Риски / предложения

- `ms=` в логе — время ВСЕГО пайплайна (embed+search+текст), не изолированный шаг
  чтения этикетки. Если для риска №1 `devops-stand-vlm.md` (тайминги упали при
  добавлении VLM, причина не доказана) нужна именно длительность шага чтения —
  отдельное поле в `PhotoScanResult`, решение ml-lead/ml-engineer (моя зона `service.py`
  без брифа не пускает).
- Системная находка вне этой задачи: любой будущий `logger.info(...)` в других модулях
  `apps/api` под боевым uvicorn так же не долетит без такого же хендлера. Предложение
  architect/devops: `logging.basicConfig(level=INFO)` в `app/main.py` один раз на весь
  процесс — дешевле точечных хендлеров по файлам (uvicorn `--log-level` не поможет: флаг
  трогает только его собственные логгеры, тоже проверено).
