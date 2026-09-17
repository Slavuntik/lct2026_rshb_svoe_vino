# ACCOUNTS — чеклист на утро (две линии CI/CD)

Кто что заводит и куда отдаёт значения, чтобы `.github/workflows/deploy-hack-yc.yml` и
`.github/workflows/deploy-biz.yml` могли реально задеплоить (сейчас — ни одного аккаунта,
оба workflow валидны и просто пропускают build/deploy без секретов, см.
`reports/e2-cicd.md`). Подробные пошаговые инструкции по каждому пункту —
`infra/deploy/hack-yandex-cloud.md` и `infra/deploy/biz-contabo.md`; здесь — только
чеклист «что завести → куда отдать значение», без команд.

**Мне (агенту) в чат — только НЕСЕКРЕТНОЕ**: публичный IP, домен, registry id — и фраза
подтверждения «секреты на месте». Сами значения секретов НИКУДА, кроме GitHub
(Settings → Secrets and variables → Actions), не вставлять — ни в чат, ни в файлы
репозитория. Это буквально то, ради чего секреты называются секретами — см. также
`agents/E2-cicd.md`, «Не делать»: «секреты не выдумывать и не коммитить».

Две колонки независимы — можно заводить хак и бизнес в любом порядке или параллельно
(например, разными людьми/в разное утро). Внутри каждой колонки порядок ВАЖЕН (нельзя
завести Container Registry раньше сервисного аккаунта, домен на Cloudflare — раньше
регистрации домена и т.д.).

## Основной чеклист

| # | Хак: Yandex Cloud | Бизнес: Proton → Namecheap → Cloudflare → Contabo |
|---|---|---|
| 1 | Аккаунт Yandex Cloud (существующий Яндекс ID или новый) | **Proton Mail** — новый ящик, корень цепочки (например `ops.svoysomelye@proton.me`) |
| 2 | Биллинг-аккаунт — привязать карту | **Namecheap** — регистрация домена НА Proton-адрес (домен нужен до шага Cloudflare) |
| 3 | Каталог (folder) — обычно `default` уже есть, можно оставить | **Cloudflare** — аккаунт на тот же Proton-адрес → Add a Site → домен из Namecheap → скопировать 2 nameserver'а → вставить их в Namecheap (Domain List → Manage → Nameservers → Custom DNS) |
| 4 | Сервисный аккаунт (SA) в IAM, роль `container-registry.images.pusher` (или `.editor`, если pusher не найдётся) | **Contabo** — заказ VPS, регион EU, 8–16 ГБ RAM, root-доступ приходит на Proton-почту |
| 5 | Авторизованный ключ SA (JSON) → скачать файл → **весь JSON → GitHub Secret `YC_SA_KEY_JSON`** | **Contabo Object Storage** — заказать отдельно в панели → endpoint/access key/secret key → **НЕ GitHub Secret, а хост `/opt/somelye/{prod,staging}/.env.backup`** (RUNBOOK §3.3) |
| 6 | Container Registry → создать в том же каталоге → **Registry ID → GitHub Secret `YC_CR_REGISTRY_ID`** | Проверить в GitHub: Settings → Actions → General → Workflow permissions → **разрешена запись пакетов** (`packages: write`) — это настройка репозитория, не аккаунт, `GITHUB_TOKEN` для GHCR отдельно заводить не нужно |
| 7 | Сгенерировать SSH-ключ для CI: `ssh-keygen -t ed25519 -f ci_deploy_key_hack -N ""` | Сгенерировать SSH-ключ для CI: `ssh-keygen -t ed25519 -f ci_deploy_key -N ""` (см. RUNBOOK §2.1) |
| 8 | Compute VM (Ubuntu 22.04/24.04, 2 vCPU/4–8 ГБ, публичный IP, вставить публичный ключ из п.7 при создании) → **публичный IP → GitHub Secret `YC_VM_HOST`** | Инициализация VM: пользователь `deploy`, публичный ключ из п.7 в `authorized_keys`, ufw, fail2ban, Docker, checkout репозитория ×2 (`prod`+`staging`) — RUNBOOK §2.1–§2.7 |
| 9 | **Приватная половина ключа из п.7 → GitHub Secret `YC_VM_SSH_KEY`** | Cloudflare Origin Certificate (SSL/TLS → Origin Server → Create Certificate, hostnames `<домен>`+`*.<домен>`) → **файлы origin.crt/origin.key → хост `/opt/somelye/tls/`** (RUNBOOK §2.8, НЕ в git) |
| 10 | Инициализация VM: пользователь `deploy`, ufw, Docker, git clone, самоподписанный TLS-сертификат (`infra/deploy/hack-yandex-cloud.md`, §2) | `ssh-keyscan -p 22 <IP-хоста>` с любой доверенной машины → **вывод целиком → GitHub Secret `DEPLOY_KNOWN_HOSTS`** |
| 11 | Первый ручной `docker compose up -d` (`infra/deploy/hack-yandex-cloud.md`, §2.4) | LLM-ключ — GigaChat или DeepSeek → **НЕ GitHub Secret, а строка в хост `/opt/somelye/{prod,staging}/.env`**: `LLM_PROVIDER`/`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL` |
| 12 | — | Остальные 4 GitHub Secrets из п.9 набора: **`DEPLOY_SSH_HOST`** (IP/домен VPS), **`DEPLOY_SSH_USER`** (`deploy`), **`DEPLOY_SSH_PORT`** (`22` или кастомный), **`DEPLOY_SSH_KEY`** (приватная половина ключа из п.7) |
| 13 | — | Первый ручной `docker compose up -d` ×2 (staging, затем prod) — RUNBOOK §2.9 |

## Куда именно отдать значения — сводка по GitHub Secrets

### Хак (Yandex Cloud) — `.github/workflows/deploy-hack-yc.yml`

| GitHub Secret | Значение |
|---|---|
| `YC_SA_KEY_JSON` | содержимое авторизованного ключа сервисного аккаунта (весь JSON) |
| `YC_CR_REGISTRY_ID` | ID реестра Container Registry (вид `crpXXXXXXXXXXXXXXXXXXXX`) |
| `YC_VM_HOST` | публичный IP Compute VM |
| `YC_VM_SSH_KEY` | приватная половина SSH-ключа CI (`ci_deploy_key_hack`) |

### Бизнес (Contabo) — `.github/workflows/deploy-biz.yml` (те же имена, что уже использует `deploy.yml` и `RUNBOOK.md` §3.1 — заводятся ОДИН раз на оба workflow, см. `infra/deploy/biz-contabo.md`, §0/§2.1)

| GitHub Secret | Значение |
|---|---|
| `DEPLOY_SSH_HOST` | IP или домен Contabo VPS |
| `DEPLOY_SSH_USER` | `deploy` |
| `DEPLOY_SSH_PORT` | `22` (или кастомный) |
| `DEPLOY_SSH_KEY` | приватная половина SSH-ключа CI (`ci_deploy_key`) |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -p <port> <host>` |

Секреты в обеих таблицах — **репозиторного** уровня (Settings → Secrets and variables →
Actions → Repository secrets), не уровня GitHub Environment `prod`/`staging` — иначе джобы
`guard` в обоих workflow (проверка «секреты на месте» до сборки) их не увидят.

## Что НЕ является GitHub Secret (для порядка, чтобы не искать зря)

- LLM-ключ (GigaChat/DeepSeek) — строка в `.env` НА ХОСТЕ (и хак, и биз), не в GitHub.
- Contabo Object Storage креды для бэкапов — строки в `.env.backup` НА ХОСТЕ (только биз).
- Cloudflare origin-cert (`origin.crt`/`origin.key`) — файлы НА ХОСТЕ (только биз).
- Самоподписанный TLS-сертификат хак-линии — файлы НА ХОСТЕ (только хак).

## Что передать в чат (агенту/оркестратору) после того, как всё заведено

Только это, ничего секретного:

- **Хак:** публичный IP Compute VM, Registry ID Container Registry.
- **Бизнес:** домен, IP Contabo VPS.
- Подтверждение: «секреты на месте» (после того как ВСЕ значения из таблиц выше вставлены
  в GitHub Secrets и хост-файлы `.env`/`.env.backup`/TLS — самостоятельно, руками, в
  соответствующих панелях).

С этого момента `deploy-hack-yc.yml`/`deploy-biz.yml` при следующем запуске (тег или
`workflow_dispatch`) перестанут пропускать build/deploy — джоба `guard` увидит секреты и
пропустит дальше по пайплайну.
