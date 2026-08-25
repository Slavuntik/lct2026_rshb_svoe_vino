# Агент E · Инфра и деплой

**Зона:** `infra/`, `.github/workflows/`, `reports/e-report.md`
**Опора:** схема деплоя `../docs/architecture.html`: Contabo VPS (EU) за Cloudflare,
prod + staging на одном хосте (два compose-проекта), Object Storage для бэкапов.
Docker на ЭТОЙ машине НЕТ — всё пишется и валидируется без запуска (yaml-lint, shellcheck-стиль).

## Задача

Полный комплект: compose, Dockerfile'ы, CI, скрипты деплоя/бэкапа/восстановления, runbook.

## Состав

1. `infra/compose.prod.yml` и `compose.staging.yml`: nginx (TLS за Cloudflare — origin-cert,
   статика apps/web, proxy на api), api (uvicorn), qdrant, postgres, вольюмы, healthchecks,
   restart-политики, лимиты памяти под VPS 8–16 ГБ.
2. `infra/Dockerfile.api` (multi-stage, uv), `infra/nginx.conf` (gzip, кэш статики,
   security-заголовки, 18+ не решается на nginx — это клиент).
3. `.github/workflows/ci.yml`: pytest (api, rag) + vitest + `rag eval` на мини-голдсете
   (если модели недоступны в CI — помечай job optional, задокументируй);
   `deploy.yml`: по тегу `v*` — build образов, ssh на хост, `compose pull && up -d`,
   секреты через GitHub Secrets (список — в runbook).
4. Скрипты `infra/scripts/`: `backup.sh` (pg_dump + qdrant snapshot → object storage, ночной cron),
   `restore.sh` (репетиция восстановления — пошагово), `deploy.sh` (идемпотентный),
   `freeze-demo.sh` (заморозка prod на демо-окно: запрет deploy-workflow по ярлыку).
5. `infra/RUNBOOK.md`: первый запуск на чистом Contabo (юзер, ufw, fail2ban, docker,
   Cloudflare DNS/origin-cert), список секретов, откат на предыдущий тег, восстановление
   из бэкапа, ротация ключей LLM. Всё под новые аккаунты (Proton/Namecheap/Cloudflare/Contabo).

## Тесты/валидация (обязательны)

- `python -c "import yaml; ..."`-валидация всех yml (скрипт в infra/scripts/validate.sh);
- nginx.conf прогнать через `nginx -t` НЕЛЬЗЯ (нет nginx) — сверить конфиг вручную и
  приложить в отчёт чек-лист; bash-скрипты — `bash -n` синтаксис.

## DoD

Комплект файлов полный; validate.sh зелёный; RUNBOOK покрывает путь «чистый VPS → работающий
staging» без пропущенных шагов; отчёт.

## Не делать

Ничего не деплоить и не регистрировать; секреты — только имена-плейсхолдеры.
