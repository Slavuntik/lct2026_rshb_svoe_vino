# Агент E3 · Хак-стенд на DigitalOcean ams3: образ с CV, compose, workflow, bootstrap

**Зона:** `infra/`, `.github/workflows/`, `apps/api/pyproject.toml`+`uv.lock` (ТОЛЬКО ради
CV-зависимостей образа), `reports/e3-do-ams3.md`. Коммиты только с pathspec.
Решение Вячеслава: разворачиваем хак-стенд на DigitalOcean, регион **ams3** (Amsterdam).

## Что сломано сейчас (нашёл оркестратор — проверь сам, не верь на слово)

1. `infra/Dockerfile.api` ставит только `packages/llm` + `packages/rag` — **CV-стека в
   образе нет вообще** (torch, transformers, opencv, paddleocr, sentencepiece,
   pillow-heif, qdrant-client + сам `packages/cv`). Сканер в контейнере не поднимется.
2. `infra/compose.prod.yml` (сервис `api`): нет ни одной CV-переменной
   (`IMAGE_PROVIDER`, `VERIFIER_PROVIDER`, `CV_DATA_DIR`, `CASE_DATA_DIR`,
   `CV_ABS_FLOOR`/`CV_MARGIN_FLOOR`/`CV_VERIFY_PROXIMITY`, `HF_HUB_OFFLINE`), нет томов
   под CV-индекс и кэш моделей, а `mem_limit: 2g` — **гарантированный OOM**: замер
   оркестратора на живом стенде — RSS 3.2 ГБ в работе, пик 3.9 ГБ при сборке индекса.
3. `packages/cv` без `uv.lock`; в `apps/api/pyproject.toml` cv — в optional-extras.

## Задачи

1. **Dockerfile.api**: собрать вариант с CV. Обязательно **CPU-only torch**
   (`--index-url https://download.pytorch.org/whl/cpu` или эквивалент для uv) — иначе
   в образ приедет CUDA на лишние ~2.5 ГБ. paddlepaddle — тоже CPU-сборка. Цель:
   образ ≤ 4 ГБ. Модели (SigLIP2 ~1.5 ГБ + PaddleOCR ~100 МБ) **НЕ запекать в образ** —
   они едут на том (см. п.4), образ остаётся переносимым.
2. **compose.prod.yml** (и staging по аналогии): добавить в `api` весь CV-блок env
   (значения по умолчанию — как на живом стенде оркестратора, см. ниже), тома
   `cv_index:/data/cv` (индекс) и `hf_cache:/data/models` (кэш HF+Paddle), поднять
   `mem_limit` api до **6g**, добавить healthcheck по `/v1/healthz` с ожиданием
   `warm:true`. Эталон переменных живого стенда:
   `IMAGE_PROVIDER=real VERIFIER_PROVIDER=real RAG_PROVIDER=real RAG_MODE=qdrant`
   `CV_DATA_DIR=/data/cv CASE_DATA_DIR=/data/case HF_HOME=/data/models`
   `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`.
3. **`.github/workflows/deploy-hack-do-ams3.yml`**: триггеры `workflow_dispatch` + тег
   `hack-v*`. Джобы: `guard` (нет секретов → notice и пропуск, как в deploy-hack-yc.yml)
   → tests (reuse ci.yml через workflow_call) → build&push api+web в **GHCR**
   (DigitalOcean Container Registry НЕ используем — лишний платный сервис, GHCR
   бесплатен для публичного репо; в доке одним абзацем: как перейти на DOCR, если
   захотят) → deploy по SSH на дроплет: `docker compose pull && up -d` + healthz-гейт с
   таймаутом и понятным падением. Секреты (фиксированные имена):
   `DO_HOST`, `DO_SSH_USER`, `DO_SSH_KEY`, `DO_KNOWN_HOSTS`.
4. **`infra/scripts/bootstrap-do-ams3.sh`** — первичная настройка чистой Ubuntu 24.04:
   пользователь `deploy` (+ docker-группа), ufw (22/80/443), fail2ban, swap 4G (страховка
   от пиков CV), Docker + compose-plugin, каталоги `/opt/somelye/hack/{,.env,tls}`,
   самоподписанный TLS (домена пока нет), логин в GHCR. Идемпотентный, `set -euo pipefail`,
   `bash -n` чистый.
5. **`infra/scripts/sync-cv-index.sh`** — доставка боевого CV-индекса и моделей с Mac на
   дроплет rsync'ом (`packages/cv/data/qdrant` ≈ 416 МБ + манифест; `~/.cache/huggingface`
   и `~/.paddlex` ≈ 2.2 ГБ в том моделей), с `--partial --progress`, проверкой sha256
   манифеста после копирования и понятным сообщением «сначала bootstrap». Пересборка
   индекса НА сервере — не вариант (46 минут CPU), это в доке объяснить.
6. **`infra/deploy/hack-digitalocean-ams3.md`** — пошагово с нуля: создание дроплета
   (регион ams3, Ubuntu 24.04, **минимум 4 vCPU / 8 ГБ RAM**, SSH-ключ, floating IP —
   опционально), запуск bootstrap, sync индекса, первый деплой, проверка, откат
   (предыдущий тег образа), что делать при OOM, абзац про DOCR и абзац про домен+TLS
   позже. Плюс раздел «стоимость» честно: план 8 ГБ у DO — порядка $48/мес.
7. **ACCOUNTS.md**: добавить третью колонку «Хак: DigitalOcean ams3» с именами секретов
   из п.3 (существующие колонки YC/Contabo не удалять — пометить YC как запасной путь).
8. **Валидация локально** (Docker на машине НЕТ — реальную сборку не проверить):
   все yml через `python -c "import yaml; yaml.safe_load(...)"`, все sh через `bash -n`,
   `infra/scripts/validate.sh` должен остаться зелёным (31/31 было). Если правишь
   `apps/api/pyproject.toml`/`uv.lock` — обязательно прогони полный свод тестов
   apps/api (было 228 passed/11 skipped) и убедись, что локальная разработка не сломана.

## Не делать

Никаких реальных деплоев, `ssh` наружу, `doctl`, покупок — сервера ещё нет. Секреты не
выдумывать (только имена). `git tag` не создавать. Не трогать `apps/api/app/**`,
`packages/`, `qa/`, `contracts/`, живой стенд на :8000 (он работает у Вячеслава).
Никаких отвязанных фоновых ожиданий (ORCHESTRATION.md). Отчёт ≤ 35 строк.
