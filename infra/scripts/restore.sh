#!/usr/bin/env bash
# infra/scripts/restore.sh — восстановление контура из бэкапа Object Storage, ПОШАГОВО.
#
# Это одновременно и рабочий инструмент, и репетиция восстановления (RUNBOOK требует
# отрепетировать до демо-окна, ../../docs/architecture.html: «восстановление отрепетировано
# до демо-окна»). Разрушает текущие данные Postgres/Qdrant выбранного контура — поэтому
# ВСЕГДА требует явный флаг подтверждения, даже для staging.
#
# Использование:
#   restore.sh <prod|staging> <timestamp|latest> --yes-i-am-sure
#
# timestamp — тот же формат, что пишет backup.sh: YYYYMMDDTHHMMSSZ (ключ в Object Storage:
# s3://<bucket>/<env>/<timestamp>/...). "latest" — взять самый свежий доступный бэкап env'а.
#
# Пример репетиции (делать РЕГУЛЯРНО на staging, не только один раз перед демо):
#   infra/scripts/restore.sh staging latest --yes-i-am-sure

set -euo pipefail

ENV="${1:-}"
WHEN="${2:-}"
CONFIRM="${3:-}"

usage() {
    echo "Использование: $0 <prod|staging> <timestamp|latest> --yes-i-am-sure" >&2
    exit 2
}

[[ -z "$ENV" || -z "$WHEN" ]] && usage
[[ "$ENV" != "prod" && "$ENV" != "staging" ]] && usage
if [[ "$CONFIRM" != "--yes-i-am-sure" ]]; then
    echo "Это ЗАТРЁТ текущие данные ${ENV} (Postgres + Qdrant). Повторить команду с флагом --yes-i-am-sure, когда это осознанное решение." >&2
    exit 2
fi

BASE_DIR="/opt/somelye/${ENV}"
COMPOSE_FILE="${BASE_DIR}/infra/compose.${ENV}.yml"
ENV_FILE="${BASE_DIR}/.env"
BACKUP_ENV_FILE="${BASE_DIR}/.env.backup"
WORK_DIR="${BASE_DIR}/restore_tmp"
LOG_DIR="${BASE_DIR}/logs"
LOG_FILE="${LOG_DIR}/restore.log"
COLLECTIONS=(wines knowledge wineries)

log() {
    mkdir -p "$LOG_DIR"
    printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ENV" "$*" | tee -a "$LOG_FILE"
}
fail() { log "ОШИБКА: $*"; exit 1; }
step() { log "ШАГ: $*"; }

[[ -f "$COMPOSE_FILE" ]] || fail "не найден $COMPOSE_FILE"
[[ -f "$BACKUP_ENV_FILE" ]] || fail "не найден $BACKUP_ENV_FILE (креды Object Storage)"
# shellcheck disable=SC1090
source "$BACKUP_ENV_FILE"
: "${AWS_ACCESS_KEY_ID:?}"; : "${AWS_SECRET_ACCESS_KEY:?}"; : "${S3_ENDPOINT_URL:?}"; : "${S3_BUCKET:?}"; : "${S3_REGION:?}"
export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
command -v aws >/dev/null 2>&1 || fail "aws-cli не установлен на хосте"

dc() { docker compose -p "$ENV" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

cd "$BASE_DIR"
rm -rf "$WORK_DIR"; mkdir -p "$WORK_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

# --- Шаг 1: разрешить "latest" в конкретный timestamp --------------------------------------
if [[ "$WHEN" == "latest" ]]; then
    step "ищу самый свежий бэкап s3://${S3_BUCKET}/${ENV}/"
    WHEN="$(aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${ENV}/" \
        --endpoint-url "$S3_ENDPOINT_URL" --region "$S3_REGION" \
        --query 'Contents[].Key' --output text 2>/dev/null \
        | tr '\t' '\n' | cut -d/ -f2 | sort -u | tail -1)"
    [[ -n "$WHEN" ]] || fail "не нашёл ни одного бэкапа в s3://${S3_BUCKET}/${ENV}/"
fi
log "восстанавливаю из timestamp=${WHEN}"

# --- Шаг 2: скачать бэкап -------------------------------------------------------------------
step "скачиваю s3://${S3_BUCKET}/${ENV}/${WHEN}/ -> ${WORK_DIR}"
aws s3 cp "s3://${S3_BUCKET}/${ENV}/${WHEN}/" "$WORK_DIR" --recursive \
    --endpoint-url "$S3_ENDPOINT_URL" --region "$S3_REGION"
ls -1 "$WORK_DIR" | grep -q . || fail "в ${WORK_DIR} после скачивания пусто — timestamp верный?"

# --- Шаг 3: поднять контур, если ещё не поднят (restore нужен работающий Postgres/Qdrant) ---
step "docker compose up -d (на случай, если контур не был поднят)"
dc up -d postgres qdrant
for _ in $(seq 1 20); do
    STATUS="$(docker inspect --format '{{.State.Health.Status}}' "somelye-${ENV}-postgres" 2>/dev/null || echo unknown)"
    [[ "$STATUS" == "healthy" ]] && break
    sleep 3
done
[[ "$STATUS" == "healthy" ]] || fail "postgres не стал healthy — восстанавливать некуда"

# --- Шаг 4: restore Postgres -----------------------------------------------------------------
POSTGRES_DB_NAME="$(grep -E '^POSTGRES_DB=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
POSTGRES_USER_NAME="$(grep -E '^POSTGRES_USER=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
PG_DUMP_FILE="$(find "$WORK_DIR" -maxdepth 1 -name 'pg_*.dump' | head -1)"
[[ -n "$PG_DUMP_FILE" ]] || fail "не нашёл pg_*.dump в бэкапе ${WHEN}"

step "останавливаю api, чтобы не писал в БД во время restore"
dc stop api || true

step "pg_restore --clean --if-exists ${POSTGRES_DB_NAME} <- $(basename "$PG_DUMP_FILE")"
dc exec -T postgres pg_restore -U "$POSTGRES_USER_NAME" -d "$POSTGRES_DB_NAME" --clean --if-exists --no-owner < "$PG_DUMP_FILE" \
    || log "ПРЕДУПРЕЖДЕНИЕ: pg_restore вернул ненулевой код — обычно это безобидные NOTICE о drop несуществующих объектов, но проверить лог руками"

ROW_COUNT_USERS="$(dc exec -T postgres psql -U "$POSTGRES_USER_NAME" -d "$POSTGRES_DB_NAME" -tAc 'select count(*) from users' | tr -d '[:space:]')"
log "проверка: users после restore = ${ROW_COUNT_USERS}"

# --- Шаг 5: restore Qdrant (по коллекции) -----------------------------------------------------
for col in "${COLLECTIONS[@]}"; do
    SNAPSHOT_FILE="$(find "$WORK_DIR" -maxdepth 1 -name "qdrant_${col}_*.snapshot" | head -1)"
    if [[ -z "$SNAPSHOT_FILE" ]]; then
        log "ПРЕДУПРЕЖДЕНИЕ: нет снапшота коллекции ${col} в бэкапе ${WHEN} — пропускаю (не было на момент backup?)"
        continue
    fi
    step "восстанавливаю коллекцию ${col} из $(basename "$SNAPSHOT_FILE")"
    REMOTE_PATH="/qdrant/storage/collections/${col}/snapshots/restore_${WHEN}.snapshot"
    dc exec -T qdrant mkdir -p "/qdrant/storage/collections/${col}/snapshots" 2>/dev/null || true
    dc cp "$SNAPSHOT_FILE" "qdrant:${REMOTE_PATH}"
    dc exec -T api python3 -c "
import httpx
r = httpx.put(
    'http://qdrant:6333/collections/${col}/snapshots/recover',
    json={'location': 'file://${REMOTE_PATH}'},
    timeout=300,
)
r.raise_for_status()
print(r.json())
" || fail "не удалось восстановить коллекцию ${col} из снапшота"

    POINTS_COUNT="$(dc exec -T api python3 -c "
import httpx
r = httpx.get('http://qdrant:6333/collections/${col}', timeout=30)
print(r.json().get('result', {}).get('points_count', '?'))
" 2>/dev/null || echo '?')"
    log "проверка: ${col}.points_count после restore = ${POINTS_COUNT}"
done

# --- Шаг 6: поднять всё обратно ---------------------------------------------------------------
step "docker compose up -d (api обратно в строй)"
dc up -d --remove-orphans

log "restore ${ENV} из ${WHEN} завершён. Пройти глазами: users=${ROW_COUNT_USERS}, коллекции Qdrant выше."
log "Если это репетиция (staging, не настоящий инцидент) — зафиксировать результат в infra/RUNBOOK.md, раздел «Восстановление отрепетировано»."
