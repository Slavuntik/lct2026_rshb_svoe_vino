#!/usr/bin/env bash
# infra/scripts/deploy.sh — идемпотентный релиз одного контура (prod ИЛИ staging) на хосте.
#
# Запускается .github/workflows/deploy.yml по ssh, но точно так же можно руками с хоста —
# сделан командой, а не только шагом CI. Рассчитан на layout из infra/RUNBOOK.md:
#
#   /opt/somelye/<env>/
#     compose.<env>.yml, nginx.conf, postgres/         (копия/checkout репозитория)
#     .env                                             (секреты, chmod 600, НЕ в git)
#     incoming/web-<tag>.tar.gz                         (кладёт CI по scp перед вызовом)
#     releases/<tag>/web/                               (распакованная статика apps/web)
#     releases/current -> releases/<tag>/web            (симлинк, что раздаёт nginx)
#     releases/.previous_tag, releases/.previous_web    (для отката, пишет сам скрипт)
#
# Использование:
#   deploy.sh <prod|staging> <tag> [image_repo]
#
# Примеры:
#   deploy.sh staging staging-abc1234 ghcr.io/svoysomelye/svoy-somelye-api
#   deploy.sh prod v1.3.0
#
# Идемпотентность: повторный вызов с тем же <tag> безопасен — pull/up -d не пересоздают
# то, что уже соответствует конфигурации; симлинк на уже-текущий релиз не трогается;
# .env переписывается тем же значением.
#
# Заморозка демо-окна (10–21.09) — это ПРОЦЕДУРА, не скрипт: не пушить теги v* и/или
# GitHub environment protection на окружении "prod" в deploy.yml (подробно —
# infra/RUNBOOK.md, раздел «Демо-окно: заморозка прода»). Этот скрипт заморозку сам не
# проверяет и не обходит — если деплой прода запущен, он либо не был замёрожен процедурно,
# либо был явно одобрен через approval окружения "prod". staging по архитектуре всегда
# остаётся деплоящимся во время демо-окна (../../docs/architecture.html, «Демо-контур»).

set -euo pipefail

ENV="${1:-}"
TAG="${2:-}"
IMAGE_REPO="${3:-}"

usage() {
    echo "Использование: $0 <prod|staging> <tag> [image_repo]" >&2
    exit 2
}

[[ -z "$ENV" || -z "$TAG" ]] && usage
[[ "$ENV" != "prod" && "$ENV" != "staging" ]] && { echo "env должен быть prod или staging, получено: $ENV" >&2; exit 2; }

BASE_DIR="/opt/somelye/${ENV}"
COMPOSE_FILE="${BASE_DIR}/infra/compose.${ENV}.yml"
ENV_FILE="${BASE_DIR}/.env"
RELEASES_DIR="${BASE_DIR}/releases"
INCOMING_DIR="${BASE_DIR}/incoming"
LOG_DIR="${BASE_DIR}/logs"
LOG_FILE="${LOG_DIR}/deploy.log"

log() {
    mkdir -p "$LOG_DIR"
    printf '[%s] [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ENV" "$*" | tee -a "$LOG_FILE"
}

fail() {
    log "ОШИБКА: $*"
    exit 1
}

set_env_var() {
    # Идемпотентно проставляет KEY=VALUE в файле, не полагаясь на `sed -i` (разный
    # синтаксис GNU/BSD) — перезаписывает файл целиком без старой строки + новая в конце.
    local key="$1" value="$2" file="$3" tmp
    tmp="$(mktemp)"
    if [[ -f "$file" ]]; then
        grep -vE "^${key}=" "$file" > "$tmp" || true
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" "$file"
}

[[ -f "$COMPOSE_FILE" ]] || fail "не найден $COMPOSE_FILE — сначала выкатить репозиторий на хост (RUNBOOK, «Первый запуск»)"
[[ -f "$ENV_FILE" ]] || fail "не найден $ENV_FILE — создать из infra/.env.${ENV}.example (RUNBOOK, «Секреты»)"

log "старт деплоя: tag=${TAG} image_repo=${IMAGE_REPO:-<без изменений>}"

mkdir -p "$RELEASES_DIR" "$INCOMING_DIR"

# --- 1. Статика apps/web: распаковать новый релиз (если приехал tarball) ---------------
WEB_TARBALL="${INCOMING_DIR}/web-${TAG}.tar.gz"
RELEASE_WEB_DIR="${RELEASES_DIR}/${TAG}/web"

if [[ -f "$WEB_TARBALL" ]]; then
    log "распаковываю статику: ${WEB_TARBALL} -> ${RELEASE_WEB_DIR}"
    rm -rf "$RELEASE_WEB_DIR"
    mkdir -p "$RELEASE_WEB_DIR"
    tar -xzf "$WEB_TARBALL" -C "$RELEASE_WEB_DIR"
elif [[ -d "$RELEASE_WEB_DIR" ]]; then
    log "tarball для ${TAG} не найден, но релиз уже распакован ранее — использую как есть (идемпотентный повтор/откат)"
else
    fail "нет ни ${WEB_TARBALL}, ни готового ${RELEASE_WEB_DIR} — нечего деплоить"
fi

# --- 2. Запомнить предыдущее состояние для отката -----------------------------------------
PREV_TAG="$(grep -E '^API_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
PREV_WEB_TARGET=""
if [[ -L "${RELEASES_DIR}/current" ]]; then
    PREV_WEB_TARGET="$(readlink "${RELEASES_DIR}/current")"
fi
printf '%s' "${PREV_TAG:-}" > "${RELEASES_DIR}/.previous_tag"
printf '%s' "${PREV_WEB_TARGET:-}" > "${RELEASES_DIR}/.previous_web"
log "предыдущее состояние сохранено: tag=${PREV_TAG:-<нет>} web=${PREV_WEB_TARGET:-<нет>}"

# --- 3. Обновить .env новым тегом (и репозиторием образа, если передан) -------------------
set_env_var "API_IMAGE_TAG" "$TAG" "$ENV_FILE"
[[ -n "$IMAGE_REPO" ]] && set_env_var "API_IMAGE" "$IMAGE_REPO" "$ENV_FILE"

# --- 4. Подтянуть образы и поднять контур --------------------------------------------------
cd "$BASE_DIR"
log "docker compose pull"
docker compose -p "$ENV" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull

log "переключаю releases/current -> ${TAG}/web"
ln -sfn "${TAG}/web" "${RELEASES_DIR}/current"

log "docker compose up -d"
docker compose -p "$ENV" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans

# --- 5. Health-check: контейнер api здоров + nginx отдаёт /nginx-health -------------------
HEALTH_OK=0
for _ in $(seq 1 20); do
    STATUS="$(docker inspect --format '{{.State.Health.Status}}' "somelye-${ENV}-api" 2>/dev/null || echo "unknown")"
    if [[ "$STATUS" == "healthy" ]]; then
        HEALTH_OK=1
        break
    fi
    sleep 3
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
    log "api не стал healthy вовремя (статус: ${STATUS:-unknown}) — откатываю"
    ROLLBACK_TAG="$(cat "${RELEASES_DIR}/.previous_tag" 2>/dev/null || true)"
    ROLLBACK_WEB="$(cat "${RELEASES_DIR}/.previous_web" 2>/dev/null || true)"
    if [[ -n "$ROLLBACK_TAG" ]]; then
        set_env_var "API_IMAGE_TAG" "$ROLLBACK_TAG" "$ENV_FILE"
        [[ -n "$ROLLBACK_WEB" ]] && ln -sfn "$ROLLBACK_WEB" "${RELEASES_DIR}/current"
        docker compose -p "$ENV" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --remove-orphans || true
        fail "откат выполнен на tag=${ROLLBACK_TAG}. Смотреть: docker compose -p ${ENV} -f ${COMPOSE_FILE} logs api"
    else
        fail "healthcheck не прошёл, а предыдущего тега для отката нет (первый деплой?). Смотреть логи api руками."
    fi
fi

EDGE_PORT="443"; [[ "$ENV" == "staging" ]] && EDGE_PORT="8443"
if curl -ks --max-time 5 "https://127.0.0.1:${EDGE_PORT}/nginx-health" | grep -q "ok"; then
    log "edge-проверка nginx (127.0.0.1:${EDGE_PORT}/nginx-health) — OK"
else
    log "ПРЕДУПРЕЖДЕНИЕ: nginx не ответил на 127.0.0.1:${EDGE_PORT}/nginx-health (api при этом healthy) — проверить nginx руками: docker compose -p ${ENV} -f ${COMPOSE_FILE} logs nginx"
fi

# --- 6. Уборка: старые релизы статики и висящие образы -------------------------------------
log "чищу старые релизы (оставляю последние 5)"
# shellcheck disable=SC2012
ls -1t "$RELEASES_DIR" 2>/dev/null | grep -vE '^(current|\.previous_tag|\.previous_web)$' | tail -n +6 | while read -r old; do
    rm -rf "${RELEASES_DIR:?}/${old}"
    log "удалён старый релиз: ${old}"
done
docker image prune -f >/dev/null 2>&1 || true

log "деплой ${ENV} на tag=${TAG} завершён успешно"
