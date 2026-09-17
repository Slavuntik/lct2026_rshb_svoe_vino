# reports/e2-cicd.md — агент E2, CI/CD до прода в двух линиях

Бриф: `agents/E2-cicd.md`. Зона: `infra/`, `.github/`, `reports/e2-cicd.md`. Аккаунтов нет,
секреты — только имена-плейсхолдеры, реальных деплоев/сетевых вызовов не делалось.

## Что сделано

### Хак-линия (Yandex Cloud)

- **`.github/workflows/deploy-hack-yc.yml`** — trigger `workflow_dispatch` + тег `hack-v*`.
  Джобы: `tests` (реюз `ci.yml` через `workflow_call`) → `determine` (тег релиза) →
  `guard` (наличие 4 секретов) → `build-push` (образы api+web в `cr.yandex`) → `deploy`
  (ssh на YC VM, `infra/scripts/deploy-hack-yc.sh`, healthz curl-цикл).
- **`infra/scripts/deploy-hack-yc.sh`** — новый скрипт (исполняемый, `bash -n` зелёный):
  идемпотентный релиз на YC VM поверх штатного `infra/compose.prod.yml` БЕЗ изменений
  самого compose-файла. Не переиспользует `infra/scripts/deploy.sh` — три структурных
  причины перечислены в шапке скрипта (валидация env только prod|staging, tar.gz vs
  образ для web, отсутствие staging-контура на хак-VM).
- **`infra/Dockerfile.web`** — новый, образ-НОСИТЕЛЬ статической сборки `apps/web` (не
  рантайм-сервис) для Yandex Container Registry — см. «Решения по хак-линии» ниже.
- **`infra/deploy/hack-yandex-cloud.md`** — пошагово от нуля: консоль Yandex Cloud
  (аккаунт → биллинг → каталог → сервисный аккаунт+роль → авторизованный ключ → Container
  Registry → Compute VM), первичная инициализация VM (ссылками на общие с RUNBOOK пункты +
  то, что специфично для хак: самоподписанный TLS вместо Cloudflare), что проверить после
  первого деплоя, ручной откат, таблица «чем хак проще биз».

### Бизнес-линия (Contabo, альтернатива Hostinger)

- **`.github/workflows/deploy-biz.yml`** — trigger `workflow_dispatch` + тег `release-v*`.
  Джобы: `tests` (реюз `ci.yml`) → `determine` → `guard` (5 секретов `DEPLOY_*`) →
  `build-push` (api-образ в GHCR + статика `apps/web`) → `android-apk` (Capacitor debug
  APK, независимо от секретов Contabo) → `ship` (scp статики, заглушка миграций, ssh
  `infra/scripts/deploy.sh prod` — уже реализованный healthz-гейт + авто-откат).
- **`infra/deploy/biz-contabo.md`** — пошагово, но БЕЗ дублирования `infra/RUNBOOK.md`:
  таблица «шаг → пункт RUNBOOK» для аккаунтов/VM/Cloudflare, плюс новое: связь с
  `deploy.yml`, секреты для `deploy-biz.yml`, откат, Android/iOS (отдельный раздел
  «ручной шаг на Mac с Xcode»), короткий раздел отличий Hostinger (провизионинг, root-доступ,
  **Object Storage для бэкапов — нет нативного S3-совместимого у Hostinger**, нужен сторонний
  провайдер).

### Общее

- **`infra/ACCOUNTS.md`** — новый, чеклист на утро с ДВУМЯ колонками (Хак/Бизнес): что
  завести, в каком порядке, что откуда скопировать и куда именно отдать (GH Secret / файл /
  строка `.env`), плюс явный список «что передать в чат» (несекретное) и «что НЕ является
  GitHub Secret» (LLM-ключ, Object Storage креды, TLS-файлы — всё это в host `.env`/файлах,
  не в GitHub).
- **`.github/workflows/ci.yml`** — минимальная аддитивная правка: добавлен триггер
  `workflow_call:` в `on:` (без изменения `pull_request`/`push` поведения), чтобы
  `deploy-hack-yc.yml`/`deploy-biz.yml` могли реально вызывать `uses: ./.github/workflows/ci.yml`.

## Секреты по линиям (имена фиксированы)

**Хак (`YC_*`, ровно как в брифе):** `YC_SA_KEY_JSON`, `YC_CR_REGISTRY_ID`, `YC_VM_HOST`,
`YC_VM_SSH_KEY`.

**Бизнес (`DEPLOY_*`):** `DEPLOY_SSH_HOST`, `DEPLOY_SSH_USER`, `DEPLOY_SSH_PORT`,
`DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`.

## Отклонения от буквального текста брифа (зафиксировано, не решено тихо)

1. **Имена секретов биз-линии.** Бриф п.2 упоминает `DEPLOY_HOST`/`DEPLOY_SSH_KEY`/
   `DEPLOY_USER`. Я использовал уже существующие `DEPLOY_SSH_HOST`/`DEPLOY_SSH_USER`/
   `DEPLOY_SSH_PORT`/`DEPLOY_SSH_KEY`/`DEPLOY_KNOWN_HOSTS` — это ТЕ ЖЕ имена, что уже
   использует `.github/workflows/deploy.yml` и задокументированы в `infra/RUNBOOK.md` §3.1
   (написаны агентом E ДО этой задачи). `DEPLOY_SSH_KEY` в обоих вариантах совпадает
   буквально — похоже, что в брифе это сокращение по памяти, а не намеренно другая пара
   секретов для того же Contabo-хоста. Заводить второй набор имён под тот же хост означало
   бы, что Вячеслав вписывает одни и те же значения дважды под разными именами — решил не
   плодить. Если оркестратор имел в виду именно новые отдельные имена — поправить
   `infra/ACCOUNTS.md`, `infra/deploy/biz-contabo.md` и джобу `guard` в `deploy-biz.yml`.
2. **`deploy.yml` vs `deploy-biz.yml`.** В репозитории уже был `.github/workflows/deploy.yml`
   (агент E, демо-окно кейса ЛЦТ): push `main` → staging, тег `v*` → prod, тот же хост
   `/opt/somelye/prod`, тот же `deploy.sh`. Бриф просит `deploy-biz.yml` с ДРУГИМИ триггерами
   (`workflow_dispatch` + `release-v*`) — я не стал трогать/удалять/сливать с уже
   отревьюженным `deploy.yml` (вне моей зоны решать за агента E и за ревью), поэтому сейчас
   ДВА workflow умеют задеплоить один и тот же прод. Пересечение безвредно (общие секреты,
   общий idempotent-скрипт), но это стоит явно решить: либо оставить оба (v* — быстрый
   демо-хотфикс, release-v* — осознанный бизнес-релиз), либо со временем убрать
   prod-путь из `deploy.yml` в пользу `deploy-biz.yml`. Не менял `deploy.yml` сам.
3. **Хак-линия: `compose.prod.yml` переиспользован БЕЗ изменений** (буквально по брифу —
   «docker compose pull && up -d (compose.prod.yml + env)»), но бриф ТАКЖЕ просит «build&push
   образов api И web» — а `compose.prod.yml` не содержит отдельного сервиса `web` (nginx там
   раздаёт статику из bind-mount `releases/current`, наполняемого `deploy.sh` из tar.gz, а не
   из образа). Решение: `infra/Dockerfile.web` собирает образ-НОСИТЕЛЬ (`/dist` внутри, без
   рантайма), `infra/scripts/deploy-hack-yc.sh` вытаскивает из него файлы на VM тем же
   способом, каким `deploy.sh` распаковывает tar.gz — `compose.prod.yml` в итоге не тронут
   вообще, а «образ web» из брифа выполнен буквально. Альтернатива (второй edge-nginx с
   reverse-proxy на api внутри самого web-образа) была отклонена — дублировала бы
   тщательно настроенный `infra/nginx.conf` (SSE/gzip/security-заголовки, см.
   `reports/e-report.md`) второй, независимо поддерживаемой копией.
4. **TLS хак-линии.** `compose.prod.yml`/`infra/nginx.conf` требуют файлы
   `/opt/somelye/tls/{origin.crt,origin.key}` — у хак-линии нет домена/Cloudflare, поэтому
   `infra/deploy/hack-yandex-cloud.md` предписывает самоподписанный сертификат в эти же
   пути (`openssl req -x509 ...`). Не секрет и не блокер, просто новая деталь, которой не
   было в брифе явно.
5. **Гард секретов на обеих линиях.** Бриф формулирует «нет секретов → notice, не красный»
   явно только для хак-линии (п.1). Я применил тот же паттерн (джоба `guard`, downstream
   `if: needs.guard.outputs.ready == 'true'`, что даёт `skipped`, а не failed) и к
   `deploy-biz.yml` — по той же причине («аккаунтов ещё нет» верно для обеих линий
   одинаково, п.0 брифа).
6. **Миграции БД — известный гэп, не выдумано.** В `apps/api` нет мигратора (alembic и
   т.п.) — проверено grep'ом по репозиторию. `contracts/schema.sql` применяется только
   через `postgres` `docker-entrypoint-initdb.d` на пустом volume (как и раньше). Шаг
   «Миграции БД» в `deploy-biz.yml` — явная промаркированная заглушка (`::notice::` с
   объяснением), не имитация работы. Предложение к контрактам: когда появится схема
   миграций — она встаёт этим шагом, ДО деплоя нового api-образа.
7. **Контейнеры на хак-VM называются `somelye-prod-*`.** `container_name` в
   `compose.prod.yml` зашит буквально (не зависит от `-p`) — на выделенной хак-VM это
   безвредный косметический нюанс, учтён в healthcheck `deploy-hack-yc.sh` и
   задокументирован в `infra/deploy/hack-yandex-cloud.md`, §3.

## Валидация локально

```bash
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye

# 1) YAML — python yaml.safe_load во временном venv (без записи в проект, тот же приём,
#    что в infra/scripts/validate.sh, когда PyYAML не установлен глобально)
python3 -m venv /tmp/yamlcheck && /tmp/yamlcheck/bin/pip install --quiet pyyaml
for f in .github/workflows/ci.yml .github/workflows/deploy.yml \
         .github/workflows/deploy-hack-yc.yml .github/workflows/deploy-biz.yml \
         infra/compose.prod.yml infra/compose.staging.yml; do
  /tmp/yamlcheck/bin/python3 -c "import yaml,sys; yaml.safe_load(open('$f')); print('OK', '$f')"
done

# 2) bash -n на все скрипты infra/scripts/, включая новый deploy-hack-yc.sh
for f in infra/scripts/*.sh; do bash -n "$f" && echo "OK $f"; done

# 3) Полный существующий валидатор — тоже зелёный на новом наборе файлов
bash infra/scripts/validate.sh
```

Результат: все YAML синтаксически валидны (одна находка — исправлена: неэкранированное
`: ` внутри `name:` шага в `deploy-biz.yml` ломало YAML-парсинг, «mapping values are not
allowed here» — переформулировал строку без двоеточия). Все bash-скрипты — `bash -n`
зелёные. `infra/scripts/validate.sh` (уже существующий, не мой) — **31 пройдено / 0
провалено, VALIDATE: OK** — он подхватил новые workflow/скрипт автоматически (глобы по
`.github/workflows/*.yml` и `infra/scripts/*.sh`), в его `REQUIRED_FILES` новые файлы не
добавлял (не входило в задачу, список рассчитан на комплект агента E). Дополнительно —
ручной grep на предмет случайных посторонних символов (нашёлся и исправлен один стрей-
символ в `hack-yandex-cloud.md`, попавший при наборе текста) и проверка на равное число
столбцов во всех markdown-таблицах новых доков. Docker/`docker compose config`/`nginx -t`
не запускались нигде — как и предписано (недоступны на этой машине, реальных
деплоев/сети не было).

## Покрытие DoD (по пунктам брифа)

| # | Пункт | Статус |
|---|---|---|
| 1 | `deploy-hack-yc.yml`: workflow_dispatch+`hack-v*`, tests→build&push(api+web в cr.yandex)→deploy по ssh, healthz curl-цикл, секреты фиксированы, гард «не красный» | Готово |
| 2 | `deploy-biz.yml`: workflow_dispatch+`release-v*`, GHCR→ssh Contabo, compose.prod.yml, healthz-гейт+откат (через deploy.sh), джоба android-apk, iOS — раздел доки | Готово (миграции — заглушка с явным нотисом, см. «Отклонения», п.6) |
| 3 | `infra/deploy/hack-yandex-cloud.md` и `biz-contabo.md`: с нуля, GH Secrets/.env, первичная инициализация VM, Cloudflare (biz), что проверить, Hostinger-подраздел | Готово |
| 4 | `infra/ACCOUNTS.md`: чеклист на утро, две колонки, куда именно отдавать значения, что мне достаточно получить в чат | Готово |
| 5 | Валидация локально: yaml.safe_load + bash -n, список файлов в отчёт, без реальных деплоев/сети | Готово (раздел выше) |
| — | Не трогать apps/, packages/, qa/, contracts/ | Соблюдено — `git status` подтверждает: изменения только в `.github/`, `infra/`, `reports/e2-cicd.md`; чужие незакоммиченные правки (apps/api/*, packages/cv/*, оставленные параллельными агентами) не задеты и не попадут в коммит (явный pathspec) |
| — | Секреты — только имена-плейсхолдеры, без git tag, без сети, ORCHESTRATION.md (фоновые процессы) | Соблюдено — тегов не создавал, сеть не дёргал, длинных фоновых ожиданий не было (вся работа — синхронные шаги в одном вызове) |

## Блокеры / открытые вопросы для оркестратора

Не блокируют сдачу этой задачи, но требуют решения ДО первого реального прогона:

1. Согласовать п.1 и п.2 из «Отклонения» выше (имена секретов биз-линии;
   `deploy.yml` vs `deploy-biz.yml`) — оставить как есть с пояснениями или свести к одному
   пути/набору имён.
2. Роль `container-registry.images.pusher` в Yandex IAM и логин `json_key` для
   `docker login cr.yandex` в `infra/ACCOUNTS.md`/`deploy-hack-yc.yml` — задокументированы
   по памяти, без сетевой проверки (см. явные пометки в обоих файлах) — сверить с
   консолью/документацией Yandex Cloud при первом реальном прогоне.
3. Когда в `apps/api` появится настоящий мигратор БД — добавить реальный шаг в
   `deploy-biz.yml` вместо заглушки-нотиса (место уже размечено).

## Как прогнать самому

```bash
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye
bash infra/scripts/validate.sh          # ожидаемо: VALIDATE: OK, 31/31
bash -n infra/scripts/deploy-hack-yc.sh # синтаксис
```
