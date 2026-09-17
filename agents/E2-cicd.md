# Агент E2 · CI/CD до прода в двух линиях: хак (Yandex Cloud) и бизнес (Contabo/Hostinger)

**Зона:** `infra/`, `.github/`, `reports/e2-cicd.md`. Коммиты только с pathspec.
Аккаунтов ещё НЕТ (Вячеслав заводит утром): всё должно быть готово локально —
workflow'ы валидны, включаются секретами, ничего никуда реально не деплоится.

## Контекст

- Есть: `.github/workflows/ci.yml` (тесты), `infra/compose.prod.yml` и staging,
  `infra/scripts/publish-index.sh` (атомарный симлинк + откат), RUNBOOK.
- Хак-линия: демо кейса локально (по ТЗ деплой не нужен), но пилот/финал — Yandex
  Cloud. Бизнес-линия: Contabo VPS (приоритет; Hostinger — комментарием как альтернатива),
  впереди Cloudflare, клиент — PWA + Capacitor (iOS/Android).
- На Mac НЕТ Docker: локальная валидация compose — только синтаксис (python-yaml),
  сборка образов — в CI-раннерах.

## Задачи

1. **`.github/workflows/deploy-hack-yc.yml`**: trigger workflow_dispatch + тег `hack-v*`.
   Джобы: tests (реюз ci.yml через workflow_call или дублируй минимум) → build&push
   образов api и web в Yandex Container Registry (cr.yandex) → deploy по SSH на
   YC Compute VM: docker compose pull && up -d (compose.prod.yml + env), прогрев
   healthz curl-циклом. Секреты (имена фиксируй): YC_SA_KEY_JSON, YC_CR_REGISTRY_ID,
   YC_VM_HOST, YC_VM_SSH_KEY. Каждая джоба с гардом наличия секретов: нет секретов →
   step с notice «секреты не заданы, пропуск» (не красный).
2. **`.github/workflows/deploy-biz.yml`**: trigger workflow_dispatch + тег `release-v*`.
   build&push в GHCR → SSH-deploy на Contabo (DEPLOY_HOST/DEPLOY_SSH_KEY/DEPLOY_USER),
   compose.prod.yml, миграции/publish-index по RUNBOOK, healthz-гейт, откат = compose
   rollback на предыдущий тег образа (зафиксируй процедуру в доке). Джоба
   `android-apk`: Capacitor-сборка debug APK как artifact (java+gradle actions);
   iOS — НЕ собирать, отдельный раздел доки «ручной шаг на Mac с Xcode» (публикация
   с изолированного контура — см. память проекта/архитектуру).
3. **`infra/deploy/hack-yandex-cloud.md`** и **`infra/deploy/biz-contabo.md`**:
   пошагово от нуля: что создать в консоли, какие значения куда (в GH Secrets, в
   .env сервера), первая инициализация VM (docker, compose, юзер deploy, ufw),
   Cloudflare (biz): DNS+proxy+TLS strict, что проверить после первого деплоя.
   Hostinger-альтернатива — короткий подраздел отличий в biz-доке.
4. **`infra/ACCOUNTS.md`** — чеклист для Вячеслава на утро, ДВЕ колонки (хак/бизнес):
   какие аккаунты завести, в каком порядке, что откуда скопировать и КУДА ИМЕННО
   отдать (имя GH Secret / файл / строка .env). Хак: Yandex Cloud (+биллинг, каталог,
   сервисный аккаунт с ролями, Container Registry). Бизнес: Proton Mail (корень) →
   Namecheap (домен) → Cloudflare → Contabo VPS; плюс LLM-ключ (GigaChat/DeepSeek)
   строкой env. Отметь, что мне достаточно получить: значения секретов он вставляет
   в GitHub сам (Settings → Secrets), а мне в чат — только несекретное (IP, домен,
   registry id) и подтверждение «секреты на месте».
5. **Валидация локально**: все yml — python yaml.safe_load; шелл-скрипты — bash -n;
   в отчёт — список файлов + чем проверил. Реальных деплоев/сетевых вызовов НЕ делать.

## Не делать

Не трогать apps/, packages/, qa/, contracts/. Секреты не выдумывать и не коммитить
(только имена-плейсхолдеры). Тег-триггеры не создавать (никаких git tag). Фоновые
отвязанные ожидания запрещены (ORCHESTRATION.md).
