# RUNBOOK — «Свой Сомелье», демо-контур

Путь «чистый Contabo VPS → работающий staging», список секретов, откат, восстановление,
ротация ключей LLM, процедура заморозки на демо-окно. Опирается на
`../../docs/architecture.html` (разделы «Схема деплоя», «Как катим», «Демо-контур: всё
новое») — если что-то здесь расходится с архитектурой, архитектура старше и главнее,
поправить нужно этот файл.

Всё ниже — про НОВЫЕ аккаунты демо-контура (Proton → Namecheap → Cloudflare → Contabo →
GitHub). Ничего из этого раздела не выполнялось агентом E автоматически: агент E писал
файлы и статически их проверял, регистрация где-либо и заказ VPS — ручные действия
оператора (см. `agents/E-infra.md`, «Не делать»).

---

## 0. Топология в двух словах

```
Cloudflare (DNS, TLS-прокси, WAF)
        |  443 (prod) / 8443 (staging), Full (strict), origin-cert
        v
   Contabo VPS (EU, 8–16 ГБ)
        │
        ├── compose-проект "prod"    (docker compose -p prod    -f infra/compose.prod.yml)
        │     nginx · api · qdrant · postgres
        │
        └── compose-проект "staging" (docker compose -p staging -f infra/compose.staging.yml)
              nginx · api · qdrant · postgres   (тот же состав, меньше лимитов)
```

Prod и staging — два независимых compose-проекта на ОДНОМ хосте: разные имена проектов
(`-p`), разные тома, разные порты на хосте (см. таблицу портов ниже). Общий у них только
Cloudflare origin-cert (один сертификат с SAN на apex + `*.<домен>` покрывает оба).

### Порты на хосте

| Сервис    | prod                         | staging                        |
|-----------|------------------------------|---------------------------------|
| nginx     | `80`, `443` (публично)       | `8080`, `8443` (публично)       |
| api       | не публикуется (только внутр. сеть) | не публикуется             |
| qdrant    | `127.0.0.1:6333` (loopback)  | `127.0.0.1:16333` (loopback)     |
| postgres  | `127.0.0.1:5432` (loopback)  | `127.0.0.1:15432` (loopback)     |

Loopback-порты qdrant/postgres нужны для (1) `rag ingest`/`rag eval` с Mac агента A через
ssh-туннель и (2) ручной отладки прямо с хоста — наружу (Cloudflare/интернет) они не
торчат никогда. 8443 у staging — Cloudflare проксирует HTTPS и на нестандартные порты из
фиксированного списка (443/2053/2083/2087/2096/8443) на ту же DNS-запись, без доп. правил.

---

## 1. Цепочка аккаунтов (один раз, вручную, вне этого репозитория)

1. **Proton Mail** — новый почтовый ящик, корень всей цепочки (например
   `ops.svoysomelye@proton.me` — точное имя на усмотрение оператора).
2. **Namecheap** — регистрация домена на Proton-адрес выше. Домен нужен ДО шага Cloudflare.
3. **Cloudflare** — аккаунт на тот же Proton-адрес → Add a Site → домен из Namecheap.
   Cloudflare выдаст 2 nameserver-адреса → прописать их в Namecheap
   (Domain List → Manage → Nameservers → Custom DNS).
4. **Contabo** — заказ VPS, регион EU, 8–16 ГБ RAM (см. «Память: как считали» ниже — 8 ГБ
   это пол, 16 ГБ комфортный запас на демо-окно). Root-доступ приходит на Proton-почту.
5. **GitHub** — служебный аккаунт/организация на тот же Proton-адрес (или отдельный приватный
   репозиторий под контролем оператора) — сюда заводятся Actions, GHCR-образы, Secrets.
6. **Object Storage (Contabo)** — заказывается отдельно в панели Contabo, там же —
   endpoint, access key, secret key для бэкапов (см. раздел «Секреты»).

Один платёжный инструмент закрывает всю цепочку — это решается один раз, не для каждого
сервиса отдельно (см. `../../docs/architecture.html`).

iOS-публикация (TestFlight/App Store) — ОТДЕЛЬНЫЙ, изолированный контур (другой Mac,
другой Apple ID, другой IP), не пересекается с этим RUNBOOK и не входит в зону агента E —
см. `../../docs/architecture.html`, «Демо-контур: всё новое».

---

## 2. Первый запуск на чистом Contabo

Выполняется по ssh под `root` сразу после получения доступа, затем — под заведённым
пользователем `deploy`.

### 2.1 Базовая гигиена и пользователь

```bash
apt update && apt -y upgrade

adduser deploy                      # пароль — сгенерировать и убрать в менеджер паролей,
                                     # заходить по нему не планируется (см. ниже — только ключ)
usermod -aG sudo deploy

mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
# Публичный ключ пары, приватная половина которой пойдёт в GitHub Secret DEPLOY_SSH_KEY:
echo "ssh-ed25519 AAAA...СЮДА-ПУБЛИЧНЫЙ-КЛЮЧ... ci-deploy" >> /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
```

Ключ генерируется ЗАРАНЕЕ на любой доверенной машине: `ssh-keygen -t ed25519 -f ci_deploy_key
-C ci-deploy` (без passphrase — ключ будет жить в GitHub Secrets, интерактивный ввод
passphrase там невозможен). Приватную половину — в `DEPLOY_SSH_KEY` (раздел «Секреты»),
публичную — выше.

**Проверить, что вход под `deploy` по ключу работает, ДО следующего шага** (иначе можно
остаться без доступа к серверу):

```bash
ssh -i ci_deploy_key deploy@<ip-сервера>
```

Только после успешной проверки — закрутить ssh:

```bash
# /etc/ssh/sshd_config
PermitRootLogin no
PasswordAuthentication no
# Порт можно оставить 22 или сменить — если меняете, не забыть ufw allow ниже и
# DEPLOY_SSH_PORT в GitHub Secrets.

systemctl restart sshd
```

### 2.2 ufw (файрвол)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        # или ваш кастомный SSH-порт
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8080/tcp      # staging, HTTP-редирект
ufw allow 8443/tcp      # staging, HTTPS
ufw enable
```

Порты qdrant/postgres (6333/16333/5432/15432) сюда НЕ входят намеренно — они забинжены на
`127.0.0.1` (см. compose-файлы), ufw их и так не пропустит снаружи, но explicit-deny поверх
loopback-only биндинга — это уже просто defense-in-depth, не обязателен.

### 2.3 fail2ban

```bash
apt install -y fail2ban
cat >/etc/fail2ban/jail.local <<'EOF'
[sshd]
enabled = true
port = 22
maxretry = 5
bantime = 1h
EOF
systemctl enable --now fail2ban
```

Если SSH-порт нестандартный — поправить `port =` в `jail.local` соответственно.

### 2.4 Docker

Официальный apt-репозиторий (docs.docker.com), не скрипт `curl | sh`:

```bash
apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker deploy
```

(Пример для Debian — если Contabo-образ на Ubuntu, URL в `download.docker.com/linux/ubuntu`,
шаг идентичен.)

### 2.5 Прочие пакеты хоста

```bash
apt install -y git awscli
```

`git` — чтобы держать на хосте чекаут репозитория (см. 2.6): деплой кода (api-образ,
статика web) едет автоматически через `deploy.yml`, а вот правки в `infra/` (compose,
nginx.conf, сами скрипты) применяются на хост ВРУЧНУЮ через `git pull` — сознательное
упрощение: авто-применение правок к nginx/compose с прод-хоста было бы более рискованным
автоматизмом, чем оно того стоит для MVP (см. `reports/e-report.md`, раздел «Допущения»).

`awscli` — для `backup.sh`/`restore.sh` (S3-совместимый Object Storage Contabo).

### 2.6 Структура каталогов и первый checkout

```bash
su - deploy
mkdir -p /opt/somelye
sudo chown deploy:deploy /opt/somelye   # если создавали от root

git clone <URL-репозитория> /opt/somelye/prod
git clone <URL-репозитория> /opt/somelye/staging
# Два НЕЗАВИСИМЫХ чекаута — можно закатывать infra-правки сперва на staging, проверить,
# потом отдельной командой на prod. Ветку/тег для infra/ выбирает оператор вручную
# (`git checkout <ref>` в соответствующем каталоге), это не часть deploy.yml.

for env in prod staging; do
  mkdir -p /opt/somelye/$env/releases /opt/somelye/$env/incoming /opt/somelye/$env/logs
  mkdir -p /opt/somelye/$env/rag-data   # индекс RAG — публикуется отдельно, см. §5
done
sudo mkdir -p /opt/somelye/tls
sudo chown deploy:deploy /opt/somelye/tls
```

`rag-data/` до первой публикации индекса (`infra/scripts/publish-index.sh`, §5) — пустой
каталог. `compose.*.yml` монтирует `rag-data/current` (симлинка там ещё нет) в контейнер
`api` — Docker создаст `current` как пустую ДИРЕКТОРИЮ на хосте автоматически при первом
`up -d`, если её нет вообще (обычное поведение bind-mount на несуществующий путь). Это
неважно: `publish-index.sh` при первой публикации всё равно сначала создаёт настоящую
версионную папку и только потом атомарно подменяет `current` на симлинк — see §5.

### 2.7 Секреты на хосте

```bash
cp /opt/somelye/prod/infra/.env.prod.example       /opt/somelye/prod/.env
cp /opt/somelye/staging/infra/.env.staging.example /opt/somelye/staging/.env
chmod 600 /opt/somelye/prod/.env /opt/somelye/staging/.env
# Отредактировать оба .env — заполнить реальные значения вместо CHANGE_ME_* (раздел «Секреты»).

for env in prod staging; do
cat > /opt/somelye/$env/.env.backup <<'EOF'
AWS_ACCESS_KEY_ID=CHANGE_ME
AWS_SECRET_ACCESS_KEY=CHANGE_ME
S3_ENDPOINT_URL=CHANGE_ME        # из панели Contabo Object Storage, вида https://<region>.contabostorage.com
S3_BUCKET=svoy-somelye-backups
S3_REGION=eu2                    # уточнить в панели Contabo при создании Object Storage
EOF
chmod 600 /opt/somelye/$env/.env.backup
done
```

### 2.8 Cloudflare: origin-cert и режим TLS

1. Cloudflare Dashboard → выбрать домен → **SSL/TLS → Overview** → режим **Full (strict)**.
   (Не Flexible — тогда Cloudflare⇄origin идёт открытым текстом; не Full без strict —
   тогда сертификат origin не проверяется по цепочке доверия, только шифрование.)
2. **SSL/TLS → Origin Server → Create Certificate**:
   - Hostnames: `<домен>` и `*.<домен>` (второе покрывает `staging.<домен>` тем же сертификатом).
   - Key type: RSA (2048).
   - Validity: 15 лет — этот сертификат проверяет только сама Cloudflare, публичным CA
     он не является, ротация по внешним причинам не нужна.
   - Скачать **Origin Certificate** и **Private Key** — Private Key показывается ОДИН РАЗ.
3. Положить на хост (НЕ в git):
   ```bash
   # содержимое origin.crt / origin.key — вставить руками, это секрет
   sudo tee /opt/somelye/tls/origin.crt >/dev/null   # вставить PEM сертификата, Ctrl-D
   sudo tee /opt/somelye/tls/origin.key >/dev/null   # вставить PEM ключа, Ctrl-D
   sudo chmod 600 /opt/somelye/tls/origin.key
   sudo chmod 644 /opt/somelye/tls/origin.crt
   ```
4. **SSL/TLS → Edge Certificates**: включить **Always Use HTTPS**. Опционально —
   **Security → Bots → Bot Fight Mode** (free) как базовая защита демо-контура.
5. **DNS**: A-запись `<домен>` → IP VPS, статус **Proxied** (оранжевое облако);
   A-запись `staging` → тот же IP VPS, тоже **Proxied**.
   Демо доступно на `https://<домен>/` (prod) и `https://staging.<домен>:8443/` (staging) —
   порт для staging указывается явно в URL, см. таблицу портов в разделе 0.

### 2.9 Первый подъём контура

```bash
cd /opt/somelye/staging
# --env-file .env ВЕЗДЕ явно: по умолчанию Compose ищет .env рядом с compose-файлом
# (infra/), а секреты по этому RUNBOOK лежат в /opt/somelye/staging/.env, на уровень
# выше — без явного флага переменные POSTGRES_*/DATABASE_URL не проинтерполируются.
docker compose -p staging -f infra/compose.staging.yml --env-file .env pull   # если api-образ уже в GHCR
# если образа ещё нет (самый первый прогон, до первого deploy.yml) — собрать локально:
docker compose -p staging -f infra/compose.staging.yml --env-file .env build api

# Статика apps/web в первый раз тоже нужна руками, пока deploy.yml не прогонялся ни разу:
mkdir -p releases/manual/web
cp -r ../../apps/web/dist/. releases/manual/web/   # предварительно: npm run build в apps/web
ln -sfn manual/web releases/current

docker compose -p staging -f infra/compose.staging.yml --env-file .env up -d
docker compose -p staging -f infra/compose.staging.yml --env-file .env ps
curl -sk https://127.0.0.1:8443/nginx-health   # ожидаем "ok"
```

Повторить то же для `prod` (порт 443 вместо 8443, каталог `/opt/somelye/prod`). После этого
первого ручного подъёма все дальнейшие релизы едут через `.github/workflows/deploy.yml` →
`infra/scripts/deploy.sh` (раздел 4).

### Память: как считали

Лимиты в `compose.prod.yml`/`compose.staging.yml` посчитаны на пол 8 ГБ, разделённый ОБОИМИ
контурами на одном хосте одновременно (prod ≈ 3.6 ГБ лимитов, staging ≈ 2.7 ГБ, остаток —
хосту/Docker/бэкапам). Самая большая переменная — память `api`: RAG (agents/A-rag.md)
работает ВНУТРИ процесса api, не отдельным сервисом (см. `../../docs/architecture.html` —
в схеме деплоя нет отдельного rag-контейнера, только nginx/api/qdrant/postgres), а embedding
+ reranker модели грузятся в память этого процесса. По факту `packages/rag/rag/config.py`
(проверено в коде агента A) — это `fastembed` (ONNX, обычно квантованные веса) +
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (dense, 384-мерный, компактная
модель) + `jinaai/jina-reranker-v2-base-multilingual` (реранкер) — оба заметно легче
bge-m3/bge-reranker-v2-m3, на которые изначально ориентировался бриф E, так что лимит `api`
(2 ГБ прод / 1.5 ГБ staging) — с запасом, а не впритык. **Перепроверить фактическое
потребление `docker stats` на первом реальном стейджинге** всё равно стоит — особенно на
демо-окне при одновременных чате + фоновом backup — но риск переполнить лимит ниже, чем
казалось на старте.

---

## 3. Секреты — полный список

### 3.1 GitHub Secrets (Settings → Secrets and variables → Actions), репозиторий или
окружения `prod`/`staging`

| Имя | Значение | Используется в |
|---|---|---|
| `DEPLOY_SSH_HOST` | IP или домен VPS | `deploy.yml` (ship) |
| `DEPLOY_SSH_USER` | `deploy` | `deploy.yml` (ship) |
| `DEPLOY_SSH_PORT` | `22` (или кастомный) | `deploy.yml` (ship) |
| `DEPLOY_SSH_KEY` | приватная половина ключа из 2.1 | `deploy.yml` (ship) |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -p <port> <host>` | `deploy.yml` (ship) |

`GITHUB_TOKEN` для push в GHCR — выдаётся автоматически, ничего заводить не нужно, только
проверить, что у репозитория Settings → Actions → General → Workflow permissions разрешена
запись пакетов (`packages: write`, уже объявлено и в `ci.yml`/`deploy.yml`).

Получить `DEPLOY_KNOWN_HOSTS` заранее (до первого прогона `deploy.yml`), с ЛЮБОЙ доверенной
машины: `ssh-keyscan -p 22 <ip-сервера> > known_hosts_output` → содержимое файла в секрет.
Так `ssh`/`scp` в `deploy.yml` не будет соглашаться на TOFU (trust-on-first-use) вслепую.

### 3.2 Host `.env` (per-environment, `/opt/somelye/<env>/.env`, chmod 600, НЕ в git)

Полный список и назначение — `infra/.env.prod.example` / `infra/.env.staging.example`
(построчные комментарии там же). Сводно: `POSTGRES_*` (своя пара prod/staging),
`JWT_SECRET` (своя пара — иначе токен, выпущенный staging, был бы валиден и на прод),
`LLM_PROVIDER`/`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`, `GIGACHAT_CLIENT_ID`/
`GIGACHAT_CLIENT_SECRET`/`GIGACHAT_SCOPE` (contracts/llm-adapter.md), `API_IMAGE`/
`API_IMAGE_TAG` (эти два поддерживает `deploy.sh` сам, руками — только при первом запуске).

### 3.3 Host `.env.backup` (per-environment, `/opt/somelye/<env>/.env.backup`, chmod 600)

`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION` —
креды Contabo Object Storage, отдельно от `.env` контейнеров, т.к. читаются НА ХОСТЕ
скриптами `backup.sh`/`restore.sh`, а не отдаются внутрь контейнеров.

### 3.4 Файлы на хосте (не переменные окружения)

`/opt/somelye/tls/origin.crt` + `origin.key` — Cloudflare Origin CA (раздел 2.8).
`/home/deploy/.ssh/authorized_keys` — публичная половина `DEPLOY_SSH_KEY`.

---

## 4. Деплой и откат

### 4.1 Обычный релиз

- PR → `.github/workflows/ci.yml` (тесты) → merge в `main` → `deploy.yml` сам катит
  **staging** (без тега, каждый merge).
- Когда staging проверен (визуально + `infra/scripts/restore.sh staging latest
  --yes-i-am-sure` как репетиция не требуется на каждый релиз, но `rag eval`-гейт — да,
  см. `contracts/rag-interface.md`) — запушить git-тег `vX.Y.Z` → `deploy.yml` катит **prod**
  тем же образом, что уже стоит на staging.

### 4.2 Откат на предыдущий тег

Самый простой путь — повторно вызвать `deploy.sh` с предыдущим тегом (образ уже лежит в
GHCR, статика — в `releases/<предыдущий_tag>/web`, если её не почистили):

```bash
ssh deploy@<host>
/opt/somelye/prod/infra/scripts/deploy.sh prod v1.2.2 ghcr.io/OWNER/svoy-somelye-api
```

Если проблема обнаружилась СРАЗУ (в течение health-check самого `deploy.sh`) — откат
происходит АВТОМАТИЧЕСКИ: скрипт возвращает `API_IMAGE_TAG` на предыдущее значение и
переключает `releases/current` обратно, без участия человека (см. комментарии в
`infra/scripts/deploy.sh`, шаг 5). Ручной откат выше — для случая, когда проблема
проявилась позже (после того как health-check уже был зелёным).

---

## 5. Деплой индекса: Mac → staging → eval-гейт → prod

Обновлено по ревью 02 — ниже РЕАЛЬНЫЙ путь, сверенный по коду (`packages/rag/rag/base.py`,
`rag/store.py`, `rag/ingest.py`, `rag/cli.py`), не план на будущее. Два независимых шага:

**А. Векторы — сетевой Qdrant напрямую, по ssh-туннелю, БЕЗ CLI-флага** (`rag ingest`/
`rag eval` не знают флага вида `--qdrant-url` — таргет управляется ИСКЛЮЧИТЕЛЬНО через
env `QDRANT_URL`, который читает `packages/rag/rag/config.py` и передаёт в `QdrantStore`
что при `rag ingest`, что при голом `Retriever()` внутри `rag eval`):

```bash
cd packages/rag && source .venv/bin/activate

# Туннель на staging-Qdrant (порт хоста — из таблицы §0, staging = 16333, prod = 6333):
ssh -N -L 6333:127.0.0.1:16333 deploy@<host> &
TUNNEL_PID=$!

# Пишет векторы НАПРЯМУЮ в сетевой Qdrant по туннелю; локально (packages/rag/data/) при
# этом ВСЁ РАВНО остаются payloads/bm25/labels/manifest — их Qdrant не хранит (см. Б).
QDRANT_URL=http://localhost:6333 rag ingest --version 20260910.1

# Голд-сет — тоже через QDRANT_URL (rag eval строит голый Retriever(), который так же
# читает QDRANT_URL из env, см. rag/base.py::Retriever.__init__ -> QdrantStore(path=...)):
QDRANT_URL=http://localhost:6333 rag eval --bench-n 100

kill "$TUNNEL_PID"
```

Гейт: hit@8 ≥ 0.85 (`contracts/rag-interface.md`), смотреть `eval/report.json` (путь
печатается в конце `rag eval`). Не зелёно — не переходим к Б, тем более к prod.

**Б. Сайдкар-файлы (payloads/bm25/labels/manifest) — `publish-index.sh`, отдельно от
шага А.** Qdrant их не хранит, а Retriever'у они нужны с диска (`packages/rag/rag/base.py`:
`Retriever.__init__` читает `data_dir` при СТАРТЕ ПРОЦЕССА) — без этого шага `api` после
шага А будет находить вектора в Qdrant, но payloads/BM25/labels — пустыми:

```bash
DEPLOY_SSH_HOST=<ip-хоста> infra/scripts/publish-index.sh staging
```

Скрипт (см. подробные комментарии в самом файле): читает версию из
`packages/rag/data/manifest.json`, `rsync` на хост в НОВЫЙ каталог `rag-data/<version>/`,
проверяет полноту НА ХОСТЕ, только потом атомарно переключает симлинк
`rag-data/current` (`ln -sfn`), рестартует контейнер `api` (`RAG_DATA_DIR` читается один
раз при старте — простого обновления файлов под уже смонтированным путём недостаточно),
ждёт healthcheck и откатывает симлинк + рестарт автоматически, если `api` не поднялся
healthy. Держит последние 5 версий на хосте.

После зелёного eval на staging — оба шага (А потом Б) повторяются на prod-туннеле
(`-L 6333:127.0.0.1:6333`, без завершающей «1» в порту — см. таблицу портов §0, и
`infra/scripts/publish-index.sh prod` вторым шагом). Версия индекса видна в
`GET /v1/healthz` → `{"status": "ok", "index_version": "..."}` (проверено по коду —
`apps/api/app/routers/health.py`) — свериться, что после публикации `index_version`
обновился на ожидаемый.

Откат индекса (если новая версия оказалась хуже уже ПОСЛЕ того, как прошла healthcheck) —
руками на хосте, тот же приём, что использует сам скрипт при провале healthcheck:
```bash
ssh deploy@<host> "ln -sfn <предыдущая-версия> /opt/somelye/<env>/rag-data/current && \
  cd /opt/somelye/<env> && docker compose -p <env> -f infra/compose.<env>.yml --env-file .env restart api"
```
Список версий на хосте: `ssh deploy@<host> ls -1 /opt/somelye/<env>/rag-data/`.

### Прогрев моделей эмбеддинга/реранкера после рестарта api

`compose.*.yml` держит модели fastembed (`RAG_FASTEMBED_CACHE=/data/fastembed-cache`,
том `fastembed_cache`) в ИМЕНОВАННОМ Docker-томе — он переживает `restart`/пересоздание
контейнера `api` (в отличие от файловой системы самого контейнера). Но у самого тома
есть ОДНО состояние «пустой» — при первом запуске на чистом хосте, либо если том
руками удалили (`docker volume rm`). В этом состоянии модели грузятся ЛЕНИВО, при
ПЕРВОМ реальном обращении к поиску (`DenseEmbedder`/`CrossEncoderReranker._ensure_loaded()`
в `packages/rag`, вызывается не в конструкторе `Retriever`, а при первом `.embed()`/
`.rerank()`) — по замерам агента A реранкер (`jinaai/jina-reranker-v2-base-multilingual`,
~1.1 ГБ) качается с HF Hub ~112с на медленной сети. `GET /v1/healthz` эту загрузку НЕ
триггерит (индекс отвечает сразу из манифеста, до эмбеддера дело не доходит) — здоровый
healthcheck не значит прогретый кэш.

**Правило: после любого события, которое могло создать `fastembed_cache` заново с нуля
(первый `docker compose up -d` на хосте, ручное `docker volume rm somelye_<env>_fastembed_cache`)
— сделать ОДИН реальный поисковый запрос (сцена «вопрос сомелье» или скан из демо-пака,
`qa/demo_pack.py`/`qa/demo-script.md`, годится и обычный ручной запрос через клиент) ДО
того, как контур уйдёт в демо-показ.** После первого прогрева модели лежат в томе —
дальнейшие `restart api` (и обычный `deploy.sh`, и `publish-index.sh`) читают их с диска
за секунды, без сети. Особенно важно на границе заморозки демо-окна (§7 ниже) — прогреть
ДО 10.09, не полагаться, что первый прогон случится сам по себе на глазах у инвестора.

---

## 6. Бэкапы и восстановление

### 6.1 Cron (на хосте, под `deploy`)

```bash
crontab -e
```
```cron
17 2 * * * /opt/somelye/prod/infra/scripts/backup.sh prod       >> /opt/somelye/prod/logs/backup.cron.log 2>&1
41 2 * * * /opt/somelye/staging/infra/scripts/backup.sh staging >> /opt/somelye/staging/logs/backup.cron.log 2>&1
```

Что бэкапится и куда — см. заголовок `infra/scripts/backup.sh`: `pg_dump` (custom format) +
снапшот каждой коллекции Qdrant (`wines`/`knowledge`/`wineries`), заливка в Object Storage,
ретеншн 14 дней (переменная `RETENTION_DAYS`).

### 6.2 Восстановление — ОБЯЗАТЕЛЬНО отрепетировать до демо-окна

```bash
/opt/somelye/staging/infra/scripts/restore.sh staging latest --yes-i-am-sure
```

`restore.sh` пошагово (лог — в `logs/restore.log`): скачивает бэкап, гасит `api`, накатывает
`pg_restore --clean --if-exists`, восстанавливает снапшоты Qdrant по коллекциям, поднимает
всё обратно, печатает контрольные цифры (`count(*) from users`, `points_count` по каждой
коллекции). Флаг `--yes-i-am-sure` обязателен всегда, включая staging — скрипт разрушает
текущие данные контура, которому его натравили.

**Зафиксировать факт репетиции** (дата, кто запускал, что увидел в контрольных цифрах) —
можно прямо в этом файле или в `reports/e-report.md` на момент реальной репетиции; на
момент написания этого RUNBOOK — она ещё не проводилась (нечего восстанавливать, `api`/`rag`
ещё не сдали код, см. `reports/e-report.md`, раздел «Блокеры»).

---

## 7. Демо-окно: заморозка прода (10.09–21.09)

Цель: с готовности демо (10.09) и до конца окна показов (21.09) прод не меняется никем и
ничем, кроме осознанного хотфикса. Всё новое — на staging
(`../../docs/architecture.html`, «Демо-контур: всё новое»). Механизм — процедурный, БЕЗ
отдельного скрипта или флага в коде.

**Перед тем как замораживать — прогреть кэш моделей (§5, «Прогрев моделей... после
рестарта api»): сделать один реальный поисковый запрос на prod ПОСЛЕ последнего деплоя/
рестарта перед 10.09.** Иначе первый холодный старт (минуты скачивания модели с HF Hub)
рискует случиться прямо во время показа — ровно то, для чего заморозка и вводится.

1. **Дисциплина по тегам (первая и главная линия).** `deploy.yml` катит prod ТОЛЬКО по
   push git-тега `v*`. Значит, простое «не пушим теги `v*` с 10.09 по 21.09» уже само по
   себе останавливает прод-деплой — у job `ship` для prod физически нет триггера. Ничего
   настраивать заранее для этого не нужно, только соблюдать.
2. **Технический бэкстоп — GitHub Environment Protection**, на случай, если тег всё же
   запушили по ошибке (или нужен намеренный хотфикс и мы хотим, чтобы он прошёл через
   ручное подтверждение, а не тихо укатился):
   - Settings → Environments → New environment → имя ровно `prod` (должно совпасть с
     `needs.determine.outputs.env` в `.github/workflows/deploy.yml` — именно эта строка
     подставляется в `environment:` джоба `ship`). Завести ДО первого реального
     прод-релиза, не в момент инцидента.
   - Deployment protection rules → **Required reviewers** → добавить того, кто владеет
     решением о заморозке (оркестратор/Вячеслав).
   - Пока правило висит, job `ship` встаёт на паузу с кнопкой «Review deployments» в
     интерфейсе Actions — прод физически не тронется, пока ревьюер не нажмёт Approve.
   - Опционально там же: **Deployment branches and tags** → ограничить, какие теги вообще
     имеют право деплоить в `prod` (например, шаблоном `v*`) — доп. слой, не обязателен.
3. **Грубый рубильник (запасной, НЕ рекомендуется на демо-окно).** Actions → Deploy → «…» →
   Disable workflow — останавливает ОБА пути, prod и staging. Обычно это НЕ то, что нужно
   во время окна показов: staging по архитектуре должен продолжать принимать новое. Держать
   в уме как вариант «стоп всему» на случай более серьёзного инцидента, не как штатный
   инструмент заморозки демо.
4. **Кто и как снимает заморозку:**
   - 21.09 (конец окна показов) — оркестратор убирает Required reviewers у окружения `prod`
     (или сознательно оставляет как постоянную практику релизов — это уже решение не про
     демо-заморозку, а про общую политику).
   - Если нужен экстренный хотфикс прод ВО ВРЕМЯ окна — тег пушится как обычно, `ship`
     встаёт на approve, тот же ревьюер разово нажимает Approve для ЭТОГО прогона, не снимая
     правило целиком — так каждый прод-релиз в окне остаётся осознанным решением, а не
     случайностью.
5. **Отрепетировать до 10.09**: включить Required reviewers на `prod`, запушить тестовый
   тег (на тестовой копии или в специально согласованном окне) и убедиться, что экран
   approve действительно появляется, а не просто предполагается по документации.

`staging` этой заморозки никогда не касается.

---

## 8. Ротация ключей LLM

Смена провайдера или ключа — правка `LLM_PROVIDER`/`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`
(и, для gigachat, `GIGACHAT_CLIENT_ID`/`GIGACHAT_CLIENT_SECRET`) в `/opt/somelye/<env>/.env`
на хосте + перезапуск ОДНОГО сервиса (без пересборки образа и без даунтайма остальных):

```bash
cd /opt/somelye/prod
# отредактировать .env
docker compose -p prod -f infra/compose.prod.yml up -d api
```

`contracts/llm-adapter.md`: выбор драйвера — ТОЛЬКО через env, в коде API логики выбора
нет — значит ротация/смена провайдера никогда не требует нового образа, только `.env` +
`up -d api`. Компрометация ключа — тот же путь: заменить `LLM_API_KEY` на новый (старый
отозвать в кабинете провайдера) и перезапустить `api`.

---

## 9. Диагностика — быстрые команды

```bash
# статус контейнеров и их health
docker compose -p prod -f /opt/somelye/prod/infra/compose.prod.yml ps

# логи конкретного сервиса
docker compose -p prod -f /opt/somelye/prod/infra/compose.prod.yml logs -f api

# память/CPU по факту (сверить с лимитами в compose.*.yml, см. «Память: как считали»)
docker stats --no-stream

# edge-проверка (nginx жив, TLS отвечает)
curl -sk https://127.0.0.1:443/nginx-health      # prod
curl -sk https://127.0.0.1:8443/nginx-health     # staging

# версия индекса и живость api (изнутри сети — с хоста напрямую порт api не торчит)
docker compose -p prod -f /opt/somelye/prod/infra/compose.prod.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/v1/healthz').read())"
```

---

## 10. Известные допущения этого RUNBOOK

Инфраструктура писалась параллельно с кодом (`ORCHESTRATION.md`). Часть предположений уже
удалось сверить с реальным кодом A/B по ходу работы (`app.main:app` как ASGI-путь,
`/v1/healthz` -> `{status, index_version}`, а по ревью 02 — и полная цепочка сетевого
Qdrant: `RAG_PROVIDER=real` + `RAG_MODE=qdrant` + `QDRANT_URL` + `RAG_DATA_DIR`, все имена
сверены напрямую по `apps/api/app/rag/factory.py` и `packages/rag/rag/base.py`/`config.py`).
Остаётся открытым только то, что перечислено в `reports/e-report.md` («Блокеры»): нет
`apps/api/uv.lock`, нет Postgres-драйвера (`psycopg[binary]`) в `apps/api/pyproject.toml`.
Полный список допущений, рисков и блокеров — там же.
