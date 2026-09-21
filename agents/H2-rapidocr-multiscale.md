# Агент H2 · RapidOCR в два масштаба + опция 8 кропов CV — CPU-путь ~90–93% без GPU

**Зона записи:** `packages/cv/cv/ocr_rapid.py` (новый), `packages/cv/cv/verify.py`,
`packages/cv/cv/index.py`, `packages/cv/cv/text_fusion.py` (+ синхронно `qa/text_v2.py`),
`packages/cv/pyproject.toml`, `packages/cv/tests/`, `apps/api/app/cv/{service,factory}.py`,
`apps/api/app/config.py`, `apps/api/pyproject.toml`, lock-файлы (`uv lock`), `apps/api/tests/`,
`infra/ams3/{deploy.sh,somelye.env.example,README.md}`, `infra/local-check/run-check-server.sh`,
отчёт `reports/h2-rapidocr.md`. Контракт правит оркестратор по отчёту.

## Основание — `reports/cpu-path-study.md` (прочитай целиком) и `reports/h1-cpu-path.md`

H1 дал CPU-путь 85.5% через живой API (PaddleOCR на центральном кропе 640 px + двойники + гейт
винодельни). Исследование оркестратора на ams3 (4 слабых vCPU) и на 62 живых фото:

- RapidOCR (ONNX Runtime; модели PP-OCRv5: детектор **mobile**, распознаватель **eslav mobile**)
  читает кроп 640 px за **0.34 с против 4.0 с** у PaddleOCR на том же сервере, не зависит от бага
  oneDNN в paddle. Серверный детектор — 2.0 с и не лучше.
- Один масштаб RapidOCR ≈ PaddleOCR по точности (84–89%), но **объединение текстов двух масштабов
  640 + 960 px** даёт 90.3% (2 кропа CV) и **93.5% (8 кропов CV)** top-1. Мелкие слова читаются на 960,
  крупные стилизованные — на 640.
- Ещё одна пара двойников: `i→и` («Py6iH» = «Рубин») — +1 фото на тексте RapidOCR.
- VLM на CPU не окупается (нужен кроп 1024 px) — в эту задачу не входит.

Прототип: `qa/real_photos_rapidocr.py` (параметры движка — оттуда, дословно), оценка:
`qa/real_photos_cpu_path.py --ocr rapid_640,rapid_960 --union rapid_640+rapid_960`.

## Задача

1. `cv/ocr_rapid.py`: обёртка RapidOCR — центральный кроп кадра (те же доли, что H1
   `CENTER_CROP`), список масштабов (`CV_OCR_RAPID_SIZES`, дефолт `640,960`), на каждый масштаб
   свой вызов движка с `Det.limit_side_len=<масштаб>`, `Det.limit_type=max`, `Global.use_cls=False`,
   порог скора 0.5; итог — тексты масштабов через пробел. Ленивая загрузка, один экземпляр движка
   на масштаб (или один движок с параметром на вызов — если API позволяет; проверь). Сбой
   движка/импорта = пустая строка + warning (деградация, не 500).
2. `LabelVerifier`: `CV_OCR_ENGINE=paddle|rapid` (дефолт `paddle` до приёмки). При `rapid`
   `read_query_text()` отдаёт текст обёртки из п.1 (режим `CV_OCR_QUERY_MODE` для rapid не нужен —
   всегда центр). PaddleOCR при `rapid` НЕ грузится на прогреве (экономия RAM и старта на дешёвом
   CPU); грузится лениво, только если `verify()` реально понадобился собственный проход OCR
   (в слиянии `CV_FUSION_VERIFY=0` — не понадобится). Прогрев (`app/cv/factory.py::
   warm_up_label_verifier`) греет выбранный движок; `healthz.warm` — как раньше.
3. `cv/text_fusion.py` + `qa/text_v2.py`: двойник `i→и` в той же дисциплине, что остальные
   (вариант токена только когда ВСЕ буквы токена — двойники или токен смешанный; «Pinot» не трогается).
   Тест на «Py6iH» → «рубин» и на нетронутый «Pinot».
4. Опция кропов CV: `CV_FUSION_CROPS=2|8` (дефолт 2). 8 = (весь кадр, кроп детектора) + три
   центральных кропа `cwide/cmid/ctight` × (как есть, через `normalize_query`) — доли в
   `qa/real_photos_qemb.py::CROPS`; per-slug максимум по всем векторам. `embed_fusion_query()`
   возвращает кортеж из N векторов, `search_fusion(vectors=...)` принимает любой N;
   кодирование — одним батчем (`SiglipEncoder.encode_batch`). Замерь время 2 против 8 на Mac.
5. Зависимости: `rapidocr`, `onnxruntime` — в `packages/cv/pyproject.toml` (в основной набор или
   extra — как устроены paddle-зависимости, так же), обнови lock-файлы (`uv lock` в затронутых
   пакетах), убедись, что `uv sync --frozen --no-dev --extra integration` в `apps/api` проходит.
   Юнит-тесты не должны требовать rapidocr/onnxruntime (ленивый импорт + подмена), CI остаётся зелёным.
6. Модели RapidOCR скачиваются при первом создании движка. Для стенда: шаг предзагрузки в
   `infra/ams3/deploy.sh` (идемпотентный вызов создания движка под пользователем сервиса ДО
   рестарта; сеть на ams3 есть), в `somelye.env.example` — `CV_OCR_ENGINE=rapid`,
   `CV_OCR_RAPID_SIZES=640,960`, `CV_FUSION_CROPS=2` (на 4 vCPU 8 кропов — ~7 с только эмбеддинги);
   `infra/local-check/run-check-server.sh` — `CV_OCR_ENGINE=rapid`, `CV_FUSION_CROPS=8`.
7. Приёмка через живой API на Mac (индекс `packages/cv/data-d1`, `CV_MODEL=google/siglip2-base-patch16-384`,
   `CV_FUSION=1 CV_FUSION_W=0.3 CV_FUSION_TEXT_SOURCE=ocr`, свободный порт ≥ 8767; VLM не подключать):
   (а) `CV_OCR_ENGINE=rapid`, 2 кропа — цель ≥ 88% top-1 (офлайн 90.3%); (б) то же, 8 кропов — цель
   ≥ 91% (офлайн 93.5%); p50/p95 по `timing_ms` для обоих. `qa/real_photos_serve.py --flat-only`
   → `qa/real_photos_eval.py --served`. Расхождение с офлайном > 2 п.п. — разберись по фото
   (какие и почему), не списывай на шум без проверки.
8. Полные тесты `packages/cv` и `apps/api` зелёные; коммиты с pathspec.

## Не делать

VLM/шлюзы/сеть с фото — нет (кроме скачивания моделей RapidOCR и pip-зависимостей). Боевой индекс
`packages/cv/data/`, стенд ams3 и `qa/real_photos_*` (кроме чтения) не трогать. Данные кейса не
коммитить. ORCHESTRATION.md — правило фоновых процессов и ловушка `pgrep -f`.
