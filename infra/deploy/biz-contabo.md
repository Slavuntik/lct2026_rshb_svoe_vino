# Бизнес-линия: деплой на Contabo — с нуля

Прод-контур «Свой Сомелье»: Contabo VPS (EU) за Cloudflare, клиент — PWA + Capacitor
(iOS/Android). Workflow — `.github/workflows/deploy-biz.yml`. Список секретов и что кому
передать — `infra/ACCOUNTS.md`, колонка «Бизнес».

**Этот документ не дублирует `infra/RUNBOOK.md`** — RUNBOOK уже покрывает путь «чистый
Contabo → работающий контур» в деталях (аккаунты, ufw, fail2ban, Docker, структура
каталогов, Cloudflare origin-cert, секреты, откат, бэкапы). Здесь — то же самое по шагам,
но каждый шаг со ссылкой на конкретный пункт RUNBOOK вместо копипасты команд (чтобы не
разъезжались две копии одного и того же при правках), плюс то, чего в RUNBOOK ещё нет:
`deploy-biz.yml` как второй триггер релиза, Android/iOS клиент, альтернатива Hostinger.

---

## 0. deploy-biz.yml vs deploy.yml — важно понимать разницу

В репозитории уже есть `.github/workflows/deploy.yml` (снаряжён агентом E под демо-окно
кейса ЛЦТ): push в `main` → **staging** без тега, push тега `v*` → **prod**. Он катит ТОТ ЖЕ
хост и каталог `/opt/somelye/prod`, что и `deploy-biz.yml` из этой задачи.

`deploy-biz.yml` — ДОПОЛНИТЕЛЬНЫЙ, осознанно отдельный триггер для той же цели: релиз по
тегу `release-v*` или вручную (`workflow_dispatch`), без привязки к демо-циклу main→staging.
Оба workflow в итоге вызывают один и тот же `infra/scripts/deploy.sh prod <tag> <image>` на
одном и том же хосте — значит, у вас теперь ДВА способа выкатить прод. Это сознательно
зафиксировано как нестыковка для оркестратора/Вячеслава, не решено тихо переписыванием или
удалением чужого файла (см. `reports/e2-cicd.md`, «deploy.yml vs deploy-biz.yml» — там же
варианты, как это можно свести к одному пути, если два триггера окажутся лишними).

**Практическое следствие:** секреты `DEPLOY_*` — ОДНИ И ТЕ ЖЕ для обоих workflow (см. §3),
заводить их дважды не нужно.

---

## 1. Цепочка аккаунтов и первичная инициализация VM — по RUNBOOK

Полностью описано в `infra/RUNBOOK.md`, ничего не меняется под `deploy-biz.yml`:

| Шаг | Где в RUNBOOK |
|---|---|
| Proton Mail → Namecheap (домен) → Cloudflare → Contabo VPS → GitHub | §1 «Цепочка аккаунтов» |
| Пользователь `deploy`, ssh-ключ, закрытие root/пароля | §2.1 |
| ufw | §2.2 |
| fail2ban | §2.3 |
| Docker (apt-репозиторий) | §2.4 |
| git, awscli | §2.5 |
| Каталоги `/opt/somelye/{prod,staging}`, checkout | §2.6 |
| `.env` из `.env.prod.example`/`.env.staging.example` | §2.7 |
| **Cloudflare: DNS + proxy + TLS strict, origin-cert** | §2.8 |
| Первый ручной подъём контура | §2.9 |

Единственное, что стоит сделать ИМЕННО из-за появления `deploy-biz.yml`: после первого
ручного подъёма (RUNBOOK §2.9) убедиться, что `/opt/somelye/prod/.env` уже содержит рабочие
`API_IMAGE`/`API_IMAGE_TAG` — оба workflow (и `deploy.yml`, и `deploy-biz.yml`) далее правят
их сами через `infra/scripts/deploy.sh` (шаг 3 скрипта), вручную трогать не нужно.

### 1.1 Cloudflare — коротко (полностью — RUNBOOK §2.8)

1. SSL/TLS → Overview → режим **Full (strict)** (не Flexible, не Full без strict).
2. SSL/TLS → Origin Server → Create Certificate → hostnames `<домен>` и `*.<домен>` →
   скачать Origin Certificate + Private Key → положить на хост в
   `/opt/somelye/tls/{origin.crt,origin.key}` (НЕ в git).
3. SSL/TLS → Edge Certificates → Always Use HTTPS.
4. DNS: A-запись `<домен>` → IP VPS, **Proxied** (оранжевое облако); A-запись `staging` →
   тот же IP, тоже Proxied.

---

## 2. Секреты — куда какое значение (см. также `infra/ACCOUNTS.md`)

### 2.1 GitHub Secrets (Settings → Secrets and variables → Actions → **Repository secrets**)

Ровно те же 5 имён, что уже использует `deploy.yml` и что задокументированы в
`infra/RUNBOOK.md` §3.1 — `deploy-biz.yml` их переиспользует, не заводит новых:

| Имя | Значение | Откуда взять |
|---|---|---|
| `DEPLOY_SSH_HOST` | IP или домен VPS | панель Contabo |
| `DEPLOY_SSH_USER` | `deploy` | заведённый в RUNBOOK §2.1 пользователь |
| `DEPLOY_SSH_PORT` | `22` (или кастомный) | RUNBOOK §2.1, если порт менялся |
| `DEPLOY_SSH_KEY` | приватная половина ключа CI | сгенерирован в RUNBOOK §2.1 |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -p <port> <host>` | выполнить с любой доверенной машины |

**Важно (см. шапку `deploy-biz.yml`):** эти секреты должны быть заведены на уровне
**репозитория**, а не GitHub Environment `prod` — иначе джоба `guard` (проверка «секреты на
месте» до входа в approval) их не увидит и всегда решит, что секретов нет. Approval-гейт
заморозки демо-окна (RUNBOOK §7) висит через `environment: prod` только на джобе `ship`,
куда секреты по-прежнему подставляются штатно.

Отклонение от `agents/E2-cicd.md`, п.2, где секреты для этого workflow названы как
`DEPLOY_HOST`/`DEPLOY_SSH_KEY`/`DEPLOY_USER` — сознательное решение переиспользовать УЖЕ
существующие имена вместо параллельного набора для одного и того же Contabo-хоста (иначе
Вячеслав заводил бы одни и те же значения дважды под разными именами). Подробно —
`reports/e2-cicd.md`, «Отклонения от буквального текста брифа».

### 2.2 Host `.env` (`/opt/somelye/prod/.env`, НЕ в git) — RUNBOOK §3.2

`POSTGRES_*`, `JWT_SECRET` — свои случайные значения. **LLM-ключ** (GigaChat или
DeepSeek, `infra/ACCOUNTS.md`) — тоже сюда, СТРОКОЙ в `.env`, не GitHub Secret:

```
LLM_PROVIDER=deepseek          # или gigachat
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<реальный ключ>
LLM_MODEL=deepseek-chat
```

(для gigachat — `GIGACHAT_CLIENT_ID`/`GIGACHAT_CLIENT_SECRET`/`GIGACHAT_SCOPE`, см.
`infra/.env.prod.example`).

---

## 3. Что проверить после первого деплоя через `deploy-biz.yml`

- `Actions → Deploy — Business (Contabo)` — `tests`/`determine`/`guard` зелёные;
  `build-push`/`android-apk`/`ship` зелёные, ЛИБО `build-push`/`ship` серые (skipped) с
  notice «секреты не заданы» (см. §2.1) — это ожидаемо, пока секреты не заведены.
- Если настроен Required reviewers на окружении `prod` (RUNBOOK §7) — джоба `ship`
  встанет на паузу с кнопкой «Review deployments», это штатное поведение заморозки.
- На хосте: `docker compose -p prod -f infra/compose.prod.yml ps` — все `healthy`.
- `curl -sk https://127.0.0.1:443/nginx-health` → `ok` (RUNBOOK §9).
- `https://<домен>/v1/healthz` снаружи → `{"status": "ok", "index_version": ...}`.
- Артефакт `android-debug-apk` появился в Actions → этот прогон → Artifacts (см. §5).

## 4. Откат

`infra/scripts/deploy.sh` (который вызывает `ship`) уже реализует healthz-гейт и
**автоматический откат** на предыдущий тег, если api не поднялся healthy в течение
20×3с сразу после релиза (сам скрипт, шаг 5) — отдельно ничего запускать не нужно.

Если проблема проявилась ПОЗЖЕ (после того как healthcheck уже был зелёным) —
ручной откат тем же скриптом с предыдущим тегом (RUNBOOK §4.2), можно и через
`deploy-biz.yml`: `workflow_dispatch` не привязан к конкретному тегу — проще зайти на VM
напрямую:

```bash
ssh deploy@<host>
/opt/somelye/prod/infra/scripts/deploy.sh prod release-vX.Y.(Z-1) ghcr.io/OWNER/svoy-somelye-api
```

Образ предыдущего релиза должен ещё лежать в GHCR (retention по умолчанию не чистит
пакеты), статика — в `releases/release-vX.Y.(Z-1)/web`, если её не подчистили
(`deploy.sh` держит последние 5 релизов, свой шаг 6).

**Миграции БД:** на момент написания в репозитории нет отдельного механизма миграций —
`contracts/schema.sql` применяется только через `postgres` `docker-entrypoint-initdb.d`
при первом старте на ПУСТОМ volume (RUNBOOK §2.6). Откат образа api НЕ откатывает схему
БД — если релиз, который откатывают, менял схему без обратной совместимости, откат
образа один не поможет, нужно разбираться со схемой руками. Это известный гэп, а не
забытый шаг — см. `reports/e2-cicd.md`, «Предложения к контрактам».

---

## 5. Клиент: Android (CI) и iOS (ручной шаг на Mac с Xcode)

### 5.1 Android — собирается в CI, не требует секретов

Джоба `android-apk` в `deploy-biz.yml` гоняется на КАЖДЫЙ прогон (не ждёт секретов
Contabo — она никуда не деплоит, только публикует артефакт): `apps/web` → `npm run build`
→ `apps/shell` → `npx cap sync android` → `./gradlew assembleDebug` (JDK 21, Android SDK
`platforms;android-36`/`build-tools;36.0.0`, см. `apps/shell/android/variables.gradle`).

Результат — `app-debug.apk` (debug-подпись автоматическая, никаких секретов-keystore не
нужно) в Actions → нужный прогон → Artifacts → `android-debug-apk`, хранится 14 дней.
Это НЕ продакшен-релиз в Google Play (там нужна подпись release-ключом, отдельный секрет
`ANDROID_KEYSTORE`/пароли и загрузка через Play Console) — только APK для ручной установки
и проверки на устройстве. Если/когда понадобится Play Store — отдельная задача (подписанный
release-билд + Play Console, не входит в эту версию пайплайна).

### 5.2 iOS — РУЧНОЙ шаг на Mac с Xcode, не в CI

CI **намеренно не собирает iOS** (`agents/E2-cicd.md`, п.2). Причины, обе достаточные по
отдельности:

1. Публикация в App Store/TestFlight требует Apple ID и подписи, которые по архитектуре
   проекта живут на **отдельном, изолированном Mac** (другой Apple ID, другой IP) —
   см. `agents/E-infra.md` §1 («iOS-публикация... ОТДЕЛЬНЫЙ, изолированный контур...
   не пересекается с этим RUNBOOK») и память проекта. GitHub-раннеры (даже macOS) для этого
   не подходят по построению процесса, не только технически.
2. Нативная часть OCR-плагина (`apps/shell/plugins/ocr-plugin`, Swift/Vision) собирается
   через Xcode/Swift Package Manager — на Linux-раннере (где собирается `android-apk`)
   этого тулчейна нет и не будет.

Порядок ручной сборки/публикации на publish-Mac:

```bash
cd apps/web && npm ci && npm run build
cd ../shell && npm ci && npx cap sync ios
npx cap open ios          # откроет apps/shell/ios/App/App.xcodeproj в Xcode
```

Дальше в Xcode на publish-Mac: сменить bundle id с плейсхолдера `ru.svoysomelye.app`
(`apps/shell/capacitor.config.ts`, если нужен боевой id), выставить подписывающий
Apple ID/команду, Product → Archive → Distribute App (TestFlight/App Store). Ничего из
этого не автоматизируется этим репозиторием — сознательно, см. причину 1 выше.

---

## 6. Hostinger — альтернатива Contabo (короткий раздел отличий)

Приоритет — Contabo (весь документ выше). Если вместо него используется Hostinger VPS,
отличия от шагов §1–§2:

- **Панель провизионинга** — hPanel вместо панели Contabo; заказ VPS-плана с Ubuntu
  22.04/24.04 (Hostinger предлагает выбор ОС при создании, как и Contabo) — шаги §2 RUNBOOK
  (ufw/fail2ban/docker/пользователь `deploy`) переносятся 1:1, это гигиена ОС, не
  специфика провайдера.
- **Доступ root** — Hostinger обычно выдаёт root-пароль в hPanel/на почту сразу при
  заказе (без отдельного шага «дождаться письма», как у Contabo) — вход по паролю,
  закрыть его сразу после добавления ssh-ключа (RUNBOOK §2.1, тот же порядок действий).
- **Object Storage для бэкапов — ЗНАЧИМОЕ отличие.** `infra/scripts/backup.sh`/`restore.sh`
  расчитаны на S3-совместимый эндпоинт (у Contabo — встроенный Object Storage, RUNBOOK
  §3.3). Hostinger нативного S3-совместимого объектного хранилища не предлагает — нужен
  сторонний провайдер (например, Backblaze B2 или Hetzner Storage Box с S3-шлюзом) —
  завести отдельно, заполнить те же переменные `.env.backup`
  (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`S3_ENDPOINT_URL`/`S3_BUCKET`/`S3_REGION`,
  RUNBOOK §3.3) значениями ЭТОГО провайдера. Сами скрипты менять не нужно — они уже
  параметризованы через `.env.backup`.
- **Секреты GitHub** — те же 5 имён из §2.1, значения — от VPS Hostinger вместо Contabo.
  Cloudflare-шаги (§1.1) не меняются — Cloudflare не зависит от того, кто хостер origin.
- **Память/тарифы** — пересчитать лимиты `compose.prod.yml`/`compose.staging.yml`
  (RUNBOOK, «Память: как считали») под конкретный план Hostinger, если он меньше 8 ГБ.

Переключение между Contabo и Hostinger — это смена значения `DEPLOY_SSH_HOST` (и, при
необходимости, `.env.backup`) в GitHub Secrets, без изменений в коде/workflow.
