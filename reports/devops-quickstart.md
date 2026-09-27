# devops: scripts/quickstart.sh — локальный запуск для жюри одной командой

## Сделано

`scripts/quickstart.sh` (bash, macOS+Linux) + аддитивный блок `quickstart*` в
Makefile (по образцу блока `winescan-*`, цели `qa/` не тронуты) + `docs/
QUICKSTART.md` для жюри. Команды: `fast` (по умолчанию), `full`, `verify`,
`status`, `stop`. Проверка uv/python3.12/node≥22; порты `API_PORT`/`WEB_PORT`
(дефолт 8000/5173) с проверкой занятости по IPv4 И IPv6 и подсказкой владельца
(lsof); PID/логи в `.local/quickstart/` (уже в `.gitignore`).

- **fast**: `uv sync --frozen --inexact` в `apps/api` (без `packages/cv`/torch)
  + `uv pip install -e packages/rag` (лёгкий) → `RAG_PROVIDER=real` на индексе
  из репозитория, реранкер выключен (`RAG_RERANKER_MODEL=""`, экономит ~1.1 ГБ
  скачивания, честно объяснено в доке), `IMAGE_PROVIDER=mock LLM_PROVIDER=mock`.
  Веб — `VITE_API_MODE=real` на этот же API (не MSW-мок): жюри видит настоящий
  сомелье-поиск и карточку сканера-заглушки через реальный интерфейс.
- **full**: проверка `CASE_DATA_DIR` (3 распознаваемых формата эталонов, иначе
  явная ошибка со списком вариантов, не стектрейс) → печать плана и запрос
  подтверждения (`--yes` пропускает) → `uv sync --frozen --extra integration
  --inexact` → `cv build-index` (`CV_MODEL=siglip2-base-patch16-384`) → API с
  `IMAGE_PROVIDER=real VERIFIER_PROVIDER=real`. `LLM_PROVIDER=mock` и здесь —
  ключей LLM нет и не будет.
- `verify`: обёртка над `eval/participant_test.sh --endpoint` без изменений
  скрипта; дефолт — `eval/queries` (пусто на чистом клоне — сам это распознаёт
  и просит `--images-dir`).

## Честные цифры для full (не "пара минут")

Источник — `reports/g3-real-index.md` + `packages/cv/data/build_summary_*.json`
(наш реальный прогон): сборка индекса на 2054 позициях — **~29 мин** на Mac с
MPS (22 мин ракурсы + 6.5 мин эмбеддинги), **~45 мин** на CPU без ускорителя
(прод-сервер, 4 vCPU). Плюс ~1.8 ГБ пакетов (torch/transformers/PaddleOCR/
RapidOCR) и ~1.4 ГБ весов SigLIP2. В доке — итог 40 мин – 1.5 ч, без вранья.

## Проверено на чистом клоне (`/tmp`, включая финальный коммит)

fast: `/v1/healthz`→`warm:true`, `rag_index_version` = боевой `20260922.1`
(индекс реально из репозитория); веб 200; `/v1/eval/predict` на произвольных
байтах → детерминированный слаг; `verify` прогнала `participant_test.sh` до
конца на тестовых фото. Все процессы/порты погашены, сторонний процесс на 5173
(не мой) не тронут. full: все проверки `CASE_DATA_DIR` (не задан/нет папки/не
распознан формат) — чистые сообщения; с валидным `CASE_REFS_DIR` дошло до
плана и подтверждения и корректно остановилось без tty — **`uv sync --extra
integration` и скачивание моделей не запускались**, как просил тимлид.

## Инцидент во время работы (отдельной строкой по просьбе тимлида)

Баг разбора аргументов (нераспознанный аргумент молча трактовался как `fast`)
привёл к тому, что я по ошибке выполнил `fast` в **рабочем** `apps/api/.venv`
вместо клона: `uv sync --frozen` без `--inexact` снёс 94 пакета (torch/
paddleocr/...). Восстановление раздельными `uv sync` оставило 3 конфликтующих
пакета opencv в состоянии, ломавшем `import cv2` — обнаружено по сообщению
тимлида о падении `uv run pytest`. Починено `uv sync --frozen --all-extras
--dev --reinstall`; проверено — **553 passed, 12 skipped**, как в README.
Причина исправлена в скрипте: неизвестный аргумент теперь explicit ошибка, а
`fast`/`full` используют `uv sync --inexact` (добавляет недостающее, не сносит
лишнее) — безопасно даже поверх уже готового окружения. Описано в
`docs/QUICKSTART.md`, "Как это устроено технически", по прямому запросу.

## Риски / предложения

- Формат `CASE_DATA_DIR` жюри неизвестен точно — скрипт понимает 3 схемы, но не
  повторяет нашу ручную чистку коллизий (F3/D1); без неё точность может быть
  ниже README — написано честно в доке.
- `packages/rag/data/qdrant/.lock` закоммичен в git (нашёл случайно, не моя
  зона, не трогал) — на flock-семантику это не влияет, риска нет.
- README.md не ссылается на `docs/QUICKSTART.md` — не в задании, предлагаю
  тимлиду/product добавить одну строку в "Быстрый старт".

## Как воспроизвести

`git clone <репо> && cd <репо> && scripts/quickstart.sh` (или `make quickstart`);
полный режим — `CASE_DATA_DIR=... scripts/quickstart.sh full --yes`.
