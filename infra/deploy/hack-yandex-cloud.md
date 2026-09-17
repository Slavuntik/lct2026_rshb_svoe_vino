# Хак-линия: деплой на Yandex Cloud — с нуля

Пилот/финал показа кейса «Своё Вино» (ЛЦТ) — самый простой контур из двух в этом
репозитории: одна VM, без домена, без Cloudflare. Сам кейс по ТЗ демонстрируется локально
(`README.md`, «Прогон скрипта кейсодержателя») — этот документ нужен только если
понадобится показать контур вживую (пилот/финал). Workflow — `.github/workflows/deploy-hack-yc.yml`.
Список секретов и что кому передать — `infra/ACCOUNTS.md`, колонка «Хак».

Многие шаги ниже (создание пользователя `deploy`, ufw, docker, структура каталогов) —
буквально те же команды, что в `infra/RUNBOOK.md` §2 для Contabo: они не специфичны для
облака, это стандартная гигиена Ubuntu/Debian VM. Здесь они не дублируются копипастой —
только ссылки на конкретные подпункты RUNBOOK, чтобы не разъезжались две копии одного и
того же при правках.

---

## 1. Что завести в консоли Yandex Cloud (по порядку)

1. **Аккаунт Yandex Cloud** — существующий Яндекс ID или новый, любая почта Вячеслава
   (Proton не обязателен — это требование только бизнес-линии, `infra/ACCOUNTS.md`).
2. **Биллинг-аккаунт** — Yandex Cloud требует привязать способ оплаты даже под грант/пробный
   период. Один платёжный инструмент, отдельно от цепочки бизнес-линии.
3. **Каталог (folder)** — внутри дефолтного облака (cloud) консоль обычно создаёт каталог
   `default` автоматически; можно оставить его или завести отдельный `svoy-somelye-hack` —
   на усмотрение оператора, дальше все шаги — внутри ОДНОГО каталога.
4. **Сервисный аккаунт (SA)** — IAM → Сервисные аккаунты → Создать. Роль — минимально
   достаточная для пуша образов: `container-registry.images.pusher` (если такой роли нет в
   списке на момент создания — `container-registry.editor` шире, но тоже подойдёт;
   уточнить актуальный список ролей в консоли IAM, этот документ писался офлайн, без
   доступа в сеть — см. `agents/E2-cicd.md`, «Не делать»).
5. **Авторизованный ключ SA** — на странице сервисного аккаунта → «Создать новый ключ» →
   «Авторизованный ключ» (authorized key, JSON). Скачать файл целиком —
   **содержимое файла → GitHub Secret `YC_SA_KEY_JSON`** (весь JSON, не отдельные поля).
6. **Container Registry** — Container Registry → Создать реестр, в том же каталоге.
   На странице реестра — **Registry ID** (вид `crpXXXXXXXXXXXXXXXXXXXX`) →
   **GitHub Secret `YC_CR_REGISTRY_ID`**.
7. **Compute VM** — Compute Cloud → Создать ВМ:
   - Образ: Ubuntu 22.04/24.04 LTS.
   - Ресурсы: пилот/демо — 2 vCPU / 4–8 ГБ RAM достаточно (это не прод-нагрузка бизнес-линии
     с её расчётом памяти в `RUNBOOK.md`, «Память: как считали» — там речь про постоянный
     прод; здесь — временный показ).
   - Публичный IP — да (статический предпочтительнее, чтобы `YC_VM_HOST` не менялся между
     перезапусками VM).
   - SSH-ключ при создании — см. следующий пункт, проще сразу указать публичный ключ пары
     CI здесь, чем добавлять его вторым шагом.
   - После создания — **публичный IP VM → GitHub Secret `YC_VM_HOST`**.

### SSH-ключ для CI (отдельная пара, не личный ключ Вячеслава)

Тот же приём, что и в `infra/RUNBOOK.md` §2.1 для Contabo:

```bash
ssh-keygen -t ed25519 -f ci_deploy_key_hack -C ci-deploy-hack -N ""
```

Публичную половину — в поле «SSH-ключи» при создании VM (или добавить позже в
`/home/deploy/.ssh/authorized_keys` после первого входа, см. §2 ниже) под пользователем,
который ниже станет `deploy`. Приватную половину (`cat ci_deploy_key_hack`) —
**GitHub Secret `YC_VM_SSH_KEY`**.

---

## 2. Первичная инициализация VM

Выполняется по ssh под пользователем-логином образа (Yandex Cloud Marketplace-Ubuntu обычно
создаёт `yc-user` или логин, указанный при создании VM), затем — под заведённым `deploy`.

Базовая гигиена, пользователь `deploy`, ufw, fail2ban, Docker — **буквально те же команды**,
что `infra/RUNBOOK.md` §2.1–§2.5 (Contabo), с одной правкой: портов staging (8080/8443) в
ufw не нужно — на хак-VM только один контур:

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Остальное — RUNBOOK.md §2.1 (пользователь `deploy` + `authorized_keys` с публичным
ключом `ci_deploy_key_hack.pub`), §2.3 (fail2ban), §2.4 (Docker apt-репозиторий).
Пакет `awscli` из §2.5 здесь не нужен — на хак-линии нет `backup.sh`/`restore.sh`.

### 2.1 Каталоги и checkout (аналог RUNBOOK §2.6, ОДИН каталог `hack` вместо prod+staging)

```bash
su - deploy
sudo mkdir -p /opt/somelye && sudo chown deploy:deploy /opt/somelye
git clone <URL-репозитория> /opt/somelye/hack
mkdir -p /opt/somelye/hack/releases
sudo mkdir -p /opt/somelye/tls && sudo chown deploy:deploy /opt/somelye/tls
```

### 2.2 Секреты на хосте — `.env`

```bash
cp /opt/somelye/hack/infra/.env.prod.example /opt/somelye/hack/.env
chmod 600 /opt/somelye/hack/.env
```

Отредактировать `.env` — заполнить `POSTGRES_*`, `JWT_SECRET` реальными случайными
значениями (см. `infra/RUNBOOK.md` §3.2 — те же переменные, тот же смысл). `LLM_*` — можно
оставить `mock` для чистого демо-показа скана без диалога с сомелье, либо заполнить
`LLM_PROVIDER=deepseek`/`gigachat` + ключ, если показывается и чат — **это строка в `.env`
на хосте, НЕ GitHub Secret** (тот же принцип, что в `infra/ACCOUNTS.md`, колонка «Бизнес»,
про LLM-ключ). `API_IMAGE`/`API_IMAGE_TAG` трогать не нужно — их обновляет сам
`infra/scripts/deploy-hack-yc.sh` при каждом релизе.

### 2.3 TLS — самоподписанный сертификат (у хак-линии нет домена/Cloudflare)

`infra/compose.prod.yml` (используется как есть, см. «Почему без изменений» ниже) ожидает
`origin.crt`/`origin.key` в `/opt/somelye/tls/` — на бизнес-линии это Cloudflare Origin CA
(`infra/RUNBOOK.md` §2.8), здесь домена нет, поэтому — самоподписанный сертификат на голый
IP VM:

```bash
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /opt/somelye/tls/origin.key \
  -out /opt/somelye/tls/origin.crt \
  -days 825 \
  -subj "/CN=<публичный-IP-VM>"
chmod 600 /opt/somelye/tls/origin.key
chmod 644 /opt/somelye/tls/origin.crt
```

Браузер будет честно ругаться на самоподписанный сертификат при заходе на
`https://<IP>/` — ожидаемо для демо по IP; все curl-проверки в CI и в
`infra/scripts/deploy-hack-yc.sh` используют `-k`/`-sk` (игнорировать доверие
сертификата), ровно как и для Cloudflare origin-cert в скриптах биз-линии.
Если хак-контуру позже понадобится домен — можно повторить `infra/RUNBOOK.md` §2.8
(Cloudflare) один в один, заменив файлы в `/opt/somelye/tls/` на настоящие.

### 2.4 Первый ручной подъём (пока CI ни разу не прогонялся)

Аналог `infra/RUNBOOK.md` §2.9, с локальной сборкой (образов в YC CR ещё нет) и
локальной статикой (`npm run build` ещё не гонялся через CI):

```bash
cd /opt/somelye/hack
docker compose -p hack -f infra/compose.prod.yml --env-file .env build api
mkdir -p releases/manual/web
cp -r ../../apps/web/dist/. releases/manual/web/    # предварительно: npm run build в apps/web
ln -sfn manual/web releases/current
docker compose -p hack -f infra/compose.prod.yml --env-file .env up -d
docker compose -p hack -f infra/compose.prod.yml --env-file .env ps
curl -sk https://127.0.0.1/nginx-health   # ожидаем "ok"
```

После этого первого ручного подъёма — все дальнейшие релизы едут через
`.github/workflows/deploy-hack-yc.yml` → `infra/scripts/deploy-hack-yc.sh`.

---

## 3. Почему `compose.prod.yml` используется БЕЗ ИЗМЕНЕНИЙ

`agents/E2-cicd.md` явно просит гонять `docker compose pull && up -d` именно на
`compose.prod.yml` — сознательно НЕ заведён отдельный `compose.hack-yc.yml`: это тот же
набор сервисов (nginx/api/qdrant/postgres), что и на бизнес-линии, просто на отдельной VM
и с самоподписанным сертификатом вместо Cloudflare origin-cert. Плата за переиспользование —
один известный косметический нюанс: `container_name` внутри `compose.prod.yml` зашит как
`somelye-prod-*` НЕЗАВИСИМО от флага `-p` (проверено по файлу) — то есть на хак-VM
контейнеры буквально называются `somelye-prod-api` и т.п., хотя вызывается с `-p hack`.
Это безвредно (хак-VM выделенная, других compose-проектов на ней нет) и учтено в
`infra/scripts/deploy-hack-yc.sh` (healthcheck смотрит именно на `somelye-prod-api`).
Подробнее — `reports/e2-cicd.md`, «Решения по хак-линии».

Статика `apps/web` едет не tar.gz по scp (как у бизнес-линии), а ОБРАЗОМ через Yandex
Container Registry (`infra/Dockerfile.web`) — так просит бриф явно («build&push образов api
и web»). Это не рантайм-контейнер: `infra/scripts/deploy-hack-yc.sh` вытаскивает `/dist` из
образа (`docker create` + `docker cp`) в `releases/<tag>/web`, раздаёт его тот же nginx, что
и API-проксирование — см. шапку `infra/Dockerfile.web`.

---

## 4. Что проверить после первого деплоя через workflow

- `Actions → Deploy — Hack (Yandex Cloud)` — все джобы зелёные ИЛИ `guard`/`build-push`/
  `deploy` серые (skipped) с notice «секреты не заданы» — это нормально, пока секреты не
  заведены.
- На VM: `docker compose -p hack -f infra/compose.prod.yml ps` — все контейнеры `healthy`.
- `curl -sk https://<IP>/nginx-health` → `ok`.
- `curl -sk https://<IP>/v1/healthz` (изнутри контура, см. `infra/RUNBOOK.md` §9 для
  примера команды через `docker compose exec`) → `{"status": "ok", ...}`.
- Открыть `https://<IP>/` в браузере (принять предупреждение о самоподписанном
  сертификате) — карточка/лендинг грузится, скан работает.
- `docker images | grep cr.yandex` на VM — теги `api`/`web` совпадают с тегом релиза
  (`hack-vX.Y.Z` или `hack-manual-...`).

## 5. Откат

Ручной, по аналогии с `infra/RUNBOOK.md` §4.2 (бизнес-линия), но проще — один шаг назад по
тегу, вызвать тот же скрипт с ПРЕДЫДУЩИМ тегом (образы уже в YC CR, слепок web — в
`releases/<предыдущий_tag>/web`, если не подчищен):

```bash
ssh deploy@<IP> \
  "/opt/somelye/hack/infra/scripts/deploy-hack-yc.sh \
     cr.yandex/<registry-id>/svoy-somelye-api \
     cr.yandex/<registry-id>/svoy-somelye-web \
     <предыдущий-тег>"
```

Если `api` не поднялся healthy СРАЗУ после релиза — `deploy-hack-yc.sh` сам об этом громко
скажет (см. его шаг 5) и укажет на логи, но НЕ откатывает автоматически (в отличие от
`deploy.sh` биз-линии) — это сознательное упрощение хак-скрипта, задокументированное в его
шапке; при неудаче — выполнить откат вручную командой выше.

## 6. Чем хак проще биз (сводка допущений этой линии)

| | Хак (эта линия) | Бизнес (`biz-contabo.md`) |
|---|---|---|
| Домен/TLS | нет домена, самоподписанный сертификат | Namecheap + Cloudflare, origin-cert |
| known_hosts ssh | TOFU при каждом прогоне CI (`ssh-keyscan`) | закреплён секретом `DEPLOY_KNOWN_HOSTS` |
| Approval на деплой | нет (только ручной `workflow_dispatch`/тег) | `environment: prod` + Required reviewers (`RUNBOOK.md` §7) |
| Откат при неудачном healthz | ручной (скрипт только сообщает) | автоматический (`deploy.sh` сам откатывает) |
| Статика web | образ в YC CR, извлекается на VM | tar.gz по scp |
| staging-контур | нет, только один контур | есть, отдельный compose-проект |

Это допустимо для пилота/демо-показа (аккаунтов и там, и там пока нет — реальных решений
это не меняет, см. `agents/E2-cicd.md`).
