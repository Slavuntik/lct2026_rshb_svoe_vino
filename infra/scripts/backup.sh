#!/usr/bin/env bash
# infra/scripts/backup.sh — ночной бэкап одного контура (prod ИЛИ staging) в Object Storage.
#
# Использование:
#   backup.sh <prod|staging>
#
# Cron на хосте (пример, RUNBOOK «Первый запуск» — ставит crontab самого деплой-юзера):
#   17 2 * * * /opt/somelye/prod/infra/scripts/backup.sh prod    >> /opt/somelye/prod/logs/backup.cron.log 2>&1
#   41 2 * * * /opt/somelye/staging/infra/scripts/backup.sh staging >> /opt/somelye/staging/logs/backup.cron.log 2>&1
#
# Что бэкапится:
#   1. Postgres: pg_dump -Fc (custom format, restore.sh восстанавливает через pg_restore).
#   2. Qdrant: снапшот каждой коллекции (wines, knowledge, wineries — contracts/rag-interface.md)
#      через HTTP API самого Qdrant. У образа qdrant/qdrant нет curl/wget внутри — снапшот
#      триггерим изнутри контейнера api (там гарантированно есть httpx, contracts/llm-adapter.md
#      требует его для деплоев провайдера deepseek), а сам файл снапшота вытаскиваем наружу
#      через `docker compose cp` — это работает на уровне Docker CLI и не требует НИКАКИХ
#      утилит внутри контейнера qdrant.
#
# Требования на хосте (RUNBOOK, «Первый запуск»): aws-cli, настроенный на S3-совместимый
# Object Storage Contabo через переменные окружения (не `aws configure`, чтобы не писать
# креды в plaintext-конфиг вне контролируемого .env):
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_ENDPOINT_URL, S3_BUCKET, S3_REGION.
# Эти переменные лежат в /opt/somelye/<env>/.env.backup (отдельно от .env контейнеров,
# т.к. читаются ЭТИМ bash-скриптом на хосте, а не передаются внутрь контейнеров).

set -euo pipefail

ENV="${1:-}"
[[ "$ENV" != "prod" && "$ENV" != "staging" ]] && { echo "Использование: $0 <prod|staging>" >&2; exit 2; }

BASE_DIR="/opt/somelye/${ENV}"
COMPOSE_FILE="${BASE_DIR}/infra/compose.${ENV}.yml"
ENV_FILE="${BASE_DIR}/.env"
BACKUP_ENV_FILE="${BASE_DIR}/.env.backup"
WORK_DIR="${BASE_DIR}/backup_tmp"
LOG_DIR="${BASE_DIR}/logs"
LOG_FILE="${LOG_DIR}/backup.log"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COLLECTIONS=(wines knowledge wineries)   # contracts/rag-interface.md

log() {
    mkdir -p "$LOG_DIR"
    printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ENV" "$*" | tee -a "$LOG_FILE"
}

fail() {
    log "ОШИБКА: $*"
    exit 1
}

[[ -f "$COMPOSE_FILE" ]] || fail "не найден $COMPOSE_FILE"
[[ -f "$BACKUP_ENV_FILE" ]] || fail "не найден $BACKUP_ENV_FILE (креды Object Storage, RUNBOOK «Секреты»)"
# shellcheck disable=SC1090
source "$BACKUP_ENV_FILE"
: "${AWS_ACCESS_KEY_ID:?нужен в $BACKUP_ENV_FILE}"
: "${AWS_SECRET_ACCESS_KEY:?нужен в $BACKUP_ENV_FILE}"
: "${S3_ENDPOINT_URL:?нужен в $BACKUP_ENV_FILE}"
: "${S3_BUCKET:?нужен в $BACKUP_ENV_FILE}"
: "${S3_REGION:?нужен в $BACKUP_ENV_FILE}"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

command -v aws >/dev/null 2>&1 || fail "aws-cli не установлен на хосте (RUNBOOK «Первый запуск» -> пакеты)"

cd "$BASE_DIR"
mkdir -p "$WORK_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

dc() { docker compose -p "$ENV" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# --- 1. Postgres: pg_dump -----------------------------------------------------------------
POSTGRES_DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
POSTGRES_USER_NAME="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
PG_DUMP_FILE="${WORK_DIR}/pg_${ENV}_${TIMESTAMP}.dump"

log "pg_dump ${POSTGRES_DB_NAME} -> ${PG_DUMP_FILE}"
dc exec -T postgres pg_dump -U "$POSTGRES_USER_NAME" -Fc "$POSTGRES_DB_NAME" > "$PG_DUMP_FILE"
[[ -s "$PG_DUMP_FILE" ]] || fail "pg_dump вернул пустой файл — не аплоаживаю подозрительный бэкап"
log "pg_dump готов: $(du -h "$PG_DUMP_FILE" | cut -f1)"

# --- 2. Qdrant: снапшот каждой коллекции ---------------------------------------------------
for col in "${COLLECTIONS[@]}"; do
    log "триггерю снапшот коллекции: ${col}"
    # Снапшот создаётся ВНУТРИ api-контейнера через httpx (см. заголовок файла) —
    # никаких утилит в самом qdrant-образе не требуется.
    SNAPSHOT_NAME="$(dc exec -T api python3 -c "
import httpx, sys
r = httpx.post('http://qdrant:6333/collections/${col}/snapshots', timeout=120)
r.raise_for_status()
name = r.json()['result']['name']
print(name)
" 2>>"$LOG_FILE" || true)"

    if [[ -z "$SNAPSHOT_NAME" ]]; then
        log "ПРЕДУПРЕЖДЕНИЕ: не удалось создать снапшот коллекции ${col} (её ещё нет? rag ingest не запускался?) — пропускаю"
        continue
    fi

    LOCAL_SNAPSHOT="${WORK_DIR}/qdrant_${col}_${TIMESTAMP}.snapshot"
    log "снапшот создан: ${SNAPSHOT_NAME} -> вытаскиваю в ${LOCAL_SNAPSHOT}"
    dc cp "qdrant:/qdrant/storage/collections/${col}/snapshots/${SNAPSHOT_NAME}" "$LOCAL_SNAPSHOT"

    # Убрать снапшот из самого Qdrant после копирования — не копить диск на VPS.
    dc exec -T api python3 -c "
import httpx
httpx.delete('http://qdrant:6333/collections/${col}/snapshots/${SNAPSHOT_NAME}', timeout=30)
" 2>>"$LOG_FILE" || log "ПРЕДУПРЕЖДЕНИЕ: не удалось удалить снапшот ${SNAPSHOT_NAME} из qdrant (не критично, переживёт до следующего backup)"
done

# --- 3. Заливка в Object Storage -----------------------------------------------------------
S3_PREFIX="s3://${S3_BUCKET}/${ENV}/${TIMESTAMP}/"
log "заливаю ${WORK_DIR} -> ${S3_PREFIX}"
aws s3 cp "$WORK_DIR" "$S3_PREFIX" --recursive --endpoint-url "$S3_ENDPOINT_URL" --region "$S3_REGION"

# --- 4. Ретеншн: удалить в Object Storage бэкапы старше RETENTION_DAYS ----------------------
log "чищу бэкапы старше ${RETENTION_DAYS} дней в s3://${S3_BUCKET}/${ENV}/"
CUTOFF_EPOCH=$(( $(date -u +%s) - RETENTION_DAYS * 86400 ))
aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${ENV}/" --endpoint-url "$S3_ENDPOINT_URL" --region "$S3_REGION" \
    --query 'Contents[].Key' --output text 2>/dev/null | tr '\t' '\n' | while read -r key; do
    [[ -z "$key" ]] && continue
    # Ключ вида <env>/<TIMESTAMP>/файл, TIMESTAMP = YYYYMMDDTHHMMSSZ
    ts_part="$(echo "$key" | cut -d/ -f2)"
    ts_epoch="$(date -u -d "${ts_part:0:8} ${ts_part:9:2}:${ts_part:11:2}:${ts_part:13:2}" +%s 2>/dev/null || echo 0)"
    if [[ "$ts_epoch" -gt 0 && "$ts_epoch" -lt "$CUTOFF_EPOCH" ]]; then
        log "удаляю устаревший бэкап: $key"
        aws s3 rm "s3://${S3_BUCKET}/${key}" --endpoint-url "$S3_ENDPOINT_URL" --region "$S3_REGION" || true
    fi
done

log "backup ${ENV} завершён успешно: ${S3_PREFIX}"
