# Отчёт агента E — Инфра и деплой

Зона: `infra/`, `.github/workflows/`, `reports/e-report.md`. Ничего не задеплоено, нигде не
зарегистрировано — весь комплект статический, проверен без Docker (на этой машине его нет).

## Замечание оркестратору: гонка при коммите (не по моей команде)

Коммит `E: инфра-комплект демо-контура...` (`b85223b`) включает, кроме файлов моей зоны,
ещё `apps/api/app/*` и `apps/api/pyproject.toml`. Я стейджил ровно `git add infra/
.github/ reports/e-report.md` (проверено — `git status --short -- infra/ .github/
reports/e-report.md` перед коммитом показывал только мои пути). `git commit` без
пути коммитит весь индекс, а не только последний `git add`; между моим `git add` и
`git commit` в тот же индекс, судя по всему, успел застейджить свои файлы агент B
(`apps/api/tests/` при этом остался неотслеженным — значит, B стейджит в несколько
шагов). Так как несколько агентов пишут в один и тот же git-индекс одной и той же
рабочей копии одновременно, порядок «add → commit» не атомарен между процессами.
Ничего не потеряно (файлы B целы и закоммичены, просто под моим сообщением коммита),
но границы коммитов смешались. Откатывать/переписывать историю сам не стал —
`git reset` на общем индексе рискует зацепить параллельно работающего агента сильнее,
чем уже случившееся смешение. Оркестратору: возможно, стоит сериализовать
add+commit между агентами (или коммитить через `git commit -- <свои пути>` с explicit
pathspec, что не спасает от гонки на `add`, но хотя бы сузит окно).

## Состав

```
infra/
  compose.prod.yml            prod: nginx, api, qdrant, postgres — healthchecks, restart,
  compose.staging.yml         staging: тот же состав, меньше лимитов, другие порты на хосте
  Dockerfile.api               multi-stage (uv), контекст = корень репо
  nginx.conf                   единый конфиг prod+staging: TLS origin-cert, gzip, кэш
                                статики, security-заголовки, SSE-прокси на /v1/chat
  RUNBOOK.md                    чистый Contabo -> работающий staging, секреты, откат,
                                восстановление, ротация LLM-ключей, заморозка демо-окна
  .env.prod.example             плейсхолдеры секретов + пояснения (prod)
  .env.staging.example          то же для staging
  postgres/init/10-extensions.sql   pgcrypto defensively, перед contracts/schema.sql
  scripts/
    validate.sh                статическая валидация всего комплекта (см. ниже)
    backup.sh                  pg_dump + снапшоты Qdrant по коллекциям -> Object Storage
    restore.sh                 пошаговое восстановление из бэкапа, требует --yes-i-am-sure
    deploy.sh                  идемпотентный релиз одного контура, авто-роллбэк по healthcheck
.github/workflows/
  ci.yml                        detect-гейт по зонам + pytest(api,rag) + vitest(web) +
                                rag-eval (опционально, continue-on-error)
  deploy.yml                    push main -> staging; тег v* -> prod (GHCR + ssh + deploy.sh),
                                environment: prod/staging — точка для GitHub Environment
                                Protection (заморозка демо, раздел RUNBOOK 7)
reports/e-report.md             этот файл
```

`freeze-demo.sh` из исходного брифа сознательно НЕ создан — по прямому уточнению
оркестратора в ходе работы заморозка демо-окна сделана процедурой (дисциплина по тегам +
GitHub Environment Protection на `prod`), без отдельного скрипта/флага. Подробности —
`infra/RUNBOOK.md`, раздел 7.

## Как валидировал

`infra/scripts/validate.sh` (bash, без Docker) — зелёный:

```
$ bash infra/scripts/validate.sh
...
Пройдено: 26, провалено: 0
VALIDATE: OK
```

Что именно проверяет:
1. YAML-синтаксис всех `*.yml`/`*.yaml` в `infra/` и `.github/workflows/` — `python3 -c
   "import yaml; yaml.safe_load_all(...)"` (PyYAML ставился во временный venv, раз в системе
   его не было — venv не остаётся в проекте).
2. `docker compose config --quiet` — best-effort, помечен SKIP: на этой машине нет
   Docker/`docker compose`, как и предполагает бриф.
3. `bash -n` на все `infra/scripts/*.sh` + проверка исполняемого бита (`chmod +x`).
4. Наличие всех обязательных файлов комплекта (список ниже, «Покрытие DoD»).
5. Эвристика на `.env.*.example`: строки `*KEY*/*SECRET*/*PASSWORD*/*TOKEN*` обязаны быть
   плейсхолдером (`CHANGE_ME...`) или пустыми — не должно быть похоже на настоящий секрет.
6. `nginx.conf`: баланс `{`/`}` (18/18) и что `ssl_certificate*` указывает на смонтированный
   `/etc/nginx/tls/`, а не на файл в репозитории.

Важная находка при первом прогоне: `validate.sh` использовал `mapfile` (bash 4+), а macOS
поставляет bash 3.2.57 (лицензия GPLv3, Apple не обновляет) — скрипт падал с `mapfile:
command not found` на этой самой машине, где его требуется гонять. Переписал на
`while read -r ... < <(find ...)` (совместимо с bash 3.2 и с bash на Linux-хосте
одновинаково) — теперь реально зелёный именно там, где по брифу и должен быть.

Отдельно (не автоматизировано, руками): прочитан весь код, реально прилетевший за время
работы в `apps/api/`, `packages/llm/`, `packages/rag/` (see «Сверка с реальным кодом» ниже) —
не для правки чужих зон, а чтобы infra не разъезжалась с реальностью там, где это дёшево
поймать сейчас.

## Чек-лист nginx.conf (вручную — `nginx -t` недоступен, nginx на машине нет)

| Пункт | Как сделано |
|---|---|
| Синтаксис/структура | Файл — набор директив для контекста `http{}` (монтируется как `conf.d/default.conf`, инклюдится СТАРШИМ `nginx.conf` образа). Баланс `{`/`}` проверен скриптом (18/18). Два `server{}`: `:80` (health + редирект на https) и `:443 ssl` (всё остальное). |
| TLS | `ssl_certificate`/`ssl_certificate_key` на `/etc/nginx/tls/origin.crt`/`origin.key` (смонтированы из `/opt/somelye/tls/`, не в репо). `TLSv1.2/1.3`, `HIGH:!aNULL:!MD5`, `ssl_prefer_server_ciphers on`, session cache/timeout заданы. OCSP stapling осознанно выключен (Origin CA проверяет только Cloudflare, публичным клиентам stapling не нужен) — прокомментировано в файле. |
| gzip | `gzip on` + список MIME (`text/*`, `application/json`, `application/javascript`, `image/svg+xml` и т.п.), `gzip_min_length 512`, `gzip_comp_level 5`. `/v1/chat` явно `gzip off` (нельзя сжимать event-stream). |
| Кэш статики | `/assets/` (хэшированные имена Vite) — `Cache-Control: public, max-age=31536000, immutable`. `/legal/` — час. `/` (index.html, SPA-фоллбэк) — `no-cache`, чтобы новый релиз подхватывался сразу. |
| Security-заголовки | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` (camera=self — под веб-скан этикетки), `Strict-Transport-Security` (180 дней, без preload — обосновано в файле), `Content-Security-Policy` — помечен как черновик, требует проверки агентами C/D после первой реальной сборки (шрифты/инлайн-стили Vite). |
| Проксирование на api | Через `resolver 127.0.0.11 + set $api_upstream` (Docker embedded DNS), а НЕ голым `proxy_pass http://api:8000` — иначе nginx закэширует IP api на старте и не заметит пересоздание контейнера при каждом `deploy.sh`. Отдельный блок `/v1/chat` без буферизации/кэша, с `proxy_read_timeout 120s` под SSE; `/v1/auth/` и `/v1/waitlist` — с доп. `limit_req` поверх лимитов уровня API. |
| 18+ | Ни одной директивы про возраст/алкоголь — сознательно, это ответственность клиента (`apps/web`), явно прокомментировано в файле. |
| SPA-роутинг | `try_files $uri $uri/ /index.html` — общий и для `/`, и для `/app` (один и тот же билд, react-router разбирается сам). |
| Тело запроса | `client_max_body_size 10m` под `/scan/ocr` (лимит контракта — 8 МБ фото + multipart-запас). |
| Health-check пути | `/nginx-health` продублирован в `:80` и `:443` намеренно — первый бьёт Docker HEALTHCHECK контейнера nginx, второй — edge-проверка из `deploy.sh` после релиза. Прокомментировано в файле. |

## Покрытие DoD

| Пункт брифа | Статус |
|---|---|
| compose.prod.yml + compose.staging.yml (nginx/api/qdrant/postgres, healthchecks, restart, лимиты) | Готово |
| Dockerfile.api (multi-stage, uv) | Готово, сверен с реальным `apps/api/app/main.py` (`app.main:app` подтвердился) |
| nginx.conf (gzip, кэш, security-заголовки) | Готово, чек-лист выше |
| ci.yml (pytest api+rag, vitest, rag eval опционально) | Готово, с detect-гейтом по готовности зон |
| deploy.yml (тег v* -> build+ssh+compose) | Готово: push main -> staging, тег v* -> prod |
| backup.sh / restore.sh / deploy.sh | Готово (freeze-demo.sh — намеренно не создан, см. ниже) |
| RUNBOOK.md: чистый VPS -> staging | Готово, включая ufw/fail2ban/docker/Cloudflare origin-cert/секреты/откат/восстановление/ротация LLM/заморозка демо |
| validate.sh зелёный | Готово (26/26), исправлена bash-3.2-несовместимость |
| Секреты — только плейсхолдеры | Готово, проверено эвристикой в validate.sh |

## Блокеры (не в моей зоне — фиксирую, не чиню молча)

1. **`apps/api/uv.lock` не закоммичен.** `infra/Dockerfile.api` требует `--frozen` (осознанно:
   сборка без лока — не воспроизводимая сборка). Пока файла нет, `docker build` на этот
   Dockerfile не соберётся. Нужно: `cd apps/api && uv lock` + коммит агентом B (или
   оркестратором). То же для `packages/rag/uv.lock`, если понадобится собирать его отдельно.
2. **Postgres-драйвер отсутствует в `apps/api/pyproject.toml`.** `apps/api/app/db.py`
   создаёт СИНХРОННЫЙ `sqlalchemy.create_engine(...)`. Я поставил в compose
   `DATABASE_URL=postgresql+psycopg://...` (psycopg3, синхронный — верный диалект под этот
   код; изначально по ошибке поставил `+asyncpg`, для sync-движка это в принципе не
   заработало бы — исправил после сверки с кодом). Но пакета `psycopg[binary]` в
   зависимостях `apps/api` пока нет — без него engine не создастся против настоящего
   Postgres (SQLite по умолчанию продолжит работать нормально, это дефолт B для дев-среды).
   Нужно: добавить `"psycopg[binary]>=3.1"` в `apps/api/pyproject.toml`.

## Предложения к контрактам / интеграционные заметки (не блокирует мою зону, но затрагивает две чужие)

1. **Сетевой Qdrant vs embedded.** Мой `compose.*.yml` поднимает `qdrant` отдельным
   сервисом (сеть, порт 6333) — прямое требование брифа E и схемы деплоя в
   `../docs/architecture.html`. По факту `packages/rag/rag/config.py` (агент A) сейчас
   реализует ТОЛЬКО embedded/локальный режим `qdrant-client` (`RAG_QDRANT_PATH`, файловый
   путь) — сетевого клиента (`url=`/`host+port=`) нет. Это разрешено брифом A для ДЕВ-среды
   («Docker НЕТ» на его машине), но моя `qdrant`-служба в compose останется неиспользуемой,
   пока `packages/rag` не научится подключаться по сети. Я поставил переменную
   `RAG_QDRANT_URL=http://qdrant:6333` на сервис `api` (по аналогии с уже принятым в
   `packages/rag` префиксом `RAG_*`) — она пока никем не читается, но инфра уже готова,
   переезжать на неё можно без правок compose/Dockerfile. Заодно завязана моя схема
   бэкапа/восстановления Qdrant (`backup.sh`/`restore.sh`) — она дергает HTTP snapshot API
   Qdrant-сервера и работать сможет только после того, как что-то реально ПИШЕТ в этот
   сервер по сети. Решение — за оркестратором: либо задача агенту A на сетевой режим,
   либо явное решение «на демо хватит embedded + бэкап тома Docker как есть» (тогда мои
   backup.sh/restore.sh для Qdrant надо будет переписать под копирование каталога данных,
   а не HTTP snapshot API — сообщаю сейчас, чтобы не готовить сюрприз на ревью).
2. **`RAG_PROVIDER=real` факгически не подключает `packages/rag`.** Агент B уже сам
   зафиксировал это как предложение к контракту в `reports/b-report.md` (символичный
   `get_retriever()` в `packages/rag`, ожидание оркестратора) — я на это никак не отвечаю,
   просто подтверждаю с infra-стороны: пока это не решено, `RAG_PROVIDER=real` в
   `.env.prod.example` ставить нельзя, там должен остаться `mock` до интеграции (я его там и
   не ставил — ни в одном `.env.*.example` нет `RAG_PROVIDER`, это чисто api-переменная вне
   моей зоны, но упоминаю, т.к. напрямую влияет на то, что реально ответит прод).
3. **`ярлык на deploy-workflow`** из первоначального брифа я прочитал (после уточнения
   оркестратора) как «GitHub Environment Protection на окружении `prod`», а не как GitHub
   PR-label (последнее не гейтит workflow, триггерящийся по тегу, у которого нет PR-контекста).
   Зафиксировано явно в RUNBOOK разделе 7 — если оркестратор имел в виду другой механизм,
   поправить именно этот раздел, остального комплекта это не касается.

## Допущения (зафиксированы явно, разумные при данной степени готовности других зон)

- Реестр образов — GHCR (`ghcr.io/<repo>-api`), не Docker Hub: не требует отдельной
  регистрации, работает на встроенном `GITHUB_TOKEN`.
- Хост-layout: `/opt/somelye/{prod,staging}/` — каждый ПОЛНЫЙ git-чекаут репозитория (не
  только `infra/`) — так относительные пути в compose (`../contracts/schema.sql` и т.п.)
  совпадают что в репо, что на хосте. Обновление `infra/` на хосте — руками (`git pull`),
  НЕ автоматизировано через `deploy.yml` (тот катит только api-образ + web-статику) —
  осознанное сужение зоны риска автоматики для MVP, см. RUNBOOK §2.5.
- Healthcheck `qdrant`: TCP-проверка через `bash -c '</dev/tcp/...'` (образ debian-slim,
  без curl/wget) — слабее полноценного HTTP-чека, задокументировано в compose-файлах с
  инструкцией, на что заменить, если в образе появится curl.
- Порты: prod 80/443 публично, staging 8080/8443 публично (Cloudflare проксирует
  нестандартные HTTPS-порты из фиксированного списка без доп. правил); qdrant/postgres —
  ТОЛЬКО `127.0.0.1` (туннель по ssh для Mac-инструментов и ad-hoc отладки), никогда наружу.
- Память: см. `RUNBOOK.md`, «Память: как считали» — пересчитана и подтверждена ПОСЛЕ того,
  как проявился реальный код `packages/rag` (модели легче, чем в исходном брифе).

## Как прогнать самому

```bash
cd /Users/vyacheslavfokin/ClaudeWorkspace/vines/svoy-somelye
bash infra/scripts/validate.sh
```

Ожидаемо: `VALIDATE: OK`, 26 пройдено / 0 провалено. Docker/`docker compose`/`nginx -t` на
этой машине не запускались нигде — по условиям задачи их тут нет.
