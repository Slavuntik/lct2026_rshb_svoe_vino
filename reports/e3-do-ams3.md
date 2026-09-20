# reports/e3-do-ams3.md — агент E3, задача переопределена на середине

Бриф: `agents/E3-do-ams3.md` (хак-стенд на DigitalOcean, регион ams3). **Остановлено
оркестратором на середине**: «ams3» — не DigitalOcean, а существующий боевой VPN-сервер
Вячеслава (exit-ams3: Reality :443, hysteria2 :8444/udp, control-plane 127.0.0.1:8090).
Всё DO-специфичное (workflow, bootstrap с ufw, дока, колонка ACCOUNTS) отброшено НЕ
создавая — на момент стоп-сигнала я как раз закончил compose.prod.yml и ещё не начинал
workflow/bootstrap/sync-скрипт/доку/ACCOUNTS.md, так что удалять нечего, только не начал.

## Что закоммичено (платформонезависимо, годится для любого Docker-хоста)

- **`infra/Dockerfile.api`** — CV-стек (`packages/cv`: torch/transformers/opencv/
  paddleocr/paddlepaddle/qdrant-client/sentencepiece/pillow-heif) добавлен через
  `--extra integration`; `libgomp1` в рантайм-стадии (opencv/paddle на CPU); healthcheck
  проверяет JSON-поле `warm==true`, не только HTTP 200.
- **`infra/compose.prod.yml` + `compose.staging.yml`**: полный CV-блок env
  (`IMAGE_PROVIDER=real VERIFIER_PROVIDER=real CV_DATA_DIR=/data/cv CASE_DATA_DIR=/data/case
  HF_HOME=/data/models HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` + пороги
  `CV_ABS_FLOOR/CV_MARGIN_FLOOR/CV_VERIFY_PROXIMITY`); именованные тома `cv_index`,
  `hf_cache` (смонтирован ДВАЖДЫ — `/data/models` и `/app/.paddlex`, т.к. paddlex игнорирует
  HF_HOME, см. `reports/g-report.md:79-80`), `case_data` (добавлен сверх буквального текста
  брифа — иначе `CASE_DATA_DIR` указывал бы в пустоту, см. ниже); `mem_limit` api
  2g→6g (prod) / 1536m→4g (staging); healthcheck на `warm:true`, `start_period` 30s→90s.
  Способ доставки индекса/моделей/case-данных на диск конкретного хоста НЕ реализован
  (это и был бы `sync-cv-index.sh`) — зависит от площадки, ждёт новый бриф.
- **`apps/api/pyproject.toml` + `uv.lock`**: CPU-only torch для Linux (`[tool.uv.sources]
  torch` → новый `[[tool.uv.index]] pytorch-cpu explicit=true`, marker
  `sys_platform=="linux"`). На macOS (dev-машина) поведение не меняется.

## Две находки по факту, не по брифу на слово

1. **`rag` не устанавливался в образ вообще** (не только `cv`). `rag`/`cv` объявлены только
   в `optional-dependencies.integration`, а `uv sync` в Dockerfile до правки шёл без
   `--extra` — `import rag` падал бы и при `RAG_PROVIDER=real`, не только CV. Один и тот же
   `--extra integration` чинит оба.
2. **torch тянул полный CUDA-стек на Linux** — проверено по `uv.lock` ДО правки: 47
   вхождений `cuda-*`/`nvidia-*` (cuda-toolkit со всеми extras, nvidia-cudnn/-cusparselt/
   -nccl/-nvshmem-cu13, triton). После правки `uv lock --upgrade-package torch` вывел
   «Removed» на все 19 этих пакетов; линуксовое колесо теперь `torch-2.14.0+cpu` с
   `download.pytorch.org/whl/cpu`, зависимости — только filelock/fsspec/jinja2/networkx/
   setuptools/sympy/typing-extensions. `paddlepaddle` (не `-gpu`) был уже чист.

## Валидация

- `uv lock --check` в `apps/api` — зелёный (134 пакета).
- `uv sync --frozen --all-extras --dev` + `uv run pytest -q` в `apps/api` — **228 passed,
  11 skipped** (совпадает с базовой линией брифа), локальная разработка не сломана.
- `infra/scripts/validate.sh` — **32 пройдено / 0 провалено** (было 31/0; +1 — чужой файл
  `.github/workflows/deploy-hack-ams3.yml`, появившийся в дереве не от меня, см. ниже, а не
  мои правки: мои 5 файлов не меняют счётчик, только проходят валидными).
- Docker недоступен — реальная сборка образа не проверялась, только статически.

## Параллельно: оркестратор уже закрыл ams3 сам

Пока я дописывал платформонезависимую часть, оркестратор напрямую закоммитил
`41eb530` — нативный (без Docker) деплой на exit-ams3: `.github/workflows/
deploy-hack-ams3.yml`, `infra/ams3/{bootstrap,deploy,push-release,sync-data}.sh`,
systemd-unit, `nginx-somelye.conf`. Не трогал эти файлы и не включал их в свой коммит —
чужой, уже смерженный коммит, вне моей зоны на этой задаче.

## Предложения / внимание оркестратору

- `mem_limit` api 6g (prod) ломает арифметику `RUNBOOK.md`, «Память: как считали» (8 ГБ на
  ОБА контура prod+staging на одном Contabo-хосте) — сам `RUNBOOK.md` не трогал (не в зоне
  этого брифа), но пересчитать стоит: prod один теперь просит ≈8 ГБ.
- `CASE_DATA_DIR`/том `case_data` — моё добавление сверх буквального списка томов брифа
  (тот называл только `cv_index`/`hf_cache`); без него верификатор near-dup молча
  деградирует до `retriever.get_by_id()` — не крашится, но эталонных имён/винтажей не будет.

## Коммит

`git commit -- infra/Dockerfile.api infra/compose.prod.yml infra/compose.staging.yml
apps/api/pyproject.toml apps/api/uv.lock reports/e3-do-ams3.md` — ровно эти 6 файлов,
никаких других путей.
