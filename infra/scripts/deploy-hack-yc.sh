#!/usr/bin/env bash
# infra/scripts/deploy-hack-yc.sh — идемпотентный релиз ХАК-линии (Yandex Cloud, ОДНА VM,
# без домена/Cloudflare — agents/E2-cicd.md, п.1) на ЭТОЙ ЖЕ VM.
#
# Это НЕ дублирует infra/scripts/deploy.sh (биз-линия) без причины — три структурных отличия
# не дали переиспользовать его как есть, не трогая уже отревьюженный скрипт биз-линии:
#   1) deploy.sh валидирует env строго в {prod, staging} и по нему же выбирает EDGE_PORT —
#      третьего варианта "hack" там нет и заводить его ради параллельной ветки означало бы
#      править чужую, уже сданную зону ради несвязанной задачи;
#   2) deploy.sh ждёт статику apps/web tar.gz'ом (incoming/web-<tag>.tar.gz по scp) — хак-линия
#      по брифу везёт web ОБРАЗОМ через Yandex Container Registry (infra/Dockerfile.web);
#   3) хак-линия всегда просто "prod-подобная" — на VM нет второго (staging) контура, откатывать
#      симлинк releases/current достаточно на один шаг назад, без раздельных сред.
# Остальное — тот же приём, что в deploy.sh/publish-index.sh (см. комментарии там):
# сохранить предыдущий тег -> обновить .env -> pull+up -d -> healthz-цикл -> явная ошибка,
# если не поднялось (ручной откат — infra/deploy/hack-yandex-cloud.md, «Откат»).
#
# Топология на VM (infra/deploy/hack-yandex-cloud.md): compose-файл — ШТАТНЫЙ
# infra/compose.prod.yml БЕЗ ИЗМЕНЕНИЙ (тот же git-checkout репозитория, что и на биз-хосте,
# только в /opt/somelye/hack, а не /opt/somelye/prod|staging — см. RUNBOOK.md §2.6, тот же
# приём). Единственное содержательное отличие хак-VM от прод-хоста — TLS: самоподписанный
# сертификат в /opt/somelye/tls/{origin.crt,origin.key} вместо Cloudflare origin-cert (нет
# домена у хак-линии), инструкция — в infra/deploy/hack-yandex-cloud.md.
#
# ВАЖНО про имена контейнеров: compose.prod.yml прописывает container_name ЖЁСТКО
# (somelye-prod-api и т.д.) НЕЗАВИСИМО от флага -p — поэтому healthcheck ниже смотрит
# именно на somelye-prod-api, хотя проект называется -p hack. Это ожидаемо и безвредно:
# хак-VM выделенная, других compose-проектов на ней нет (подробно — reports/e2-cicd.md).
#
# Использование:
#   deploy-hack-yc.sh <api_image_ref> <web_image_ref> <tag>
# Пример (именно так его вызывает .github/workflows/deploy-hack-yc.yml, джоба deploy):
#   deploy-hack-yc.sh cr.yandex/crXXXXXXXXXXXX/svoy-somelye-api \
#                     cr.yandex/crXXXXXXXXXXXX/svoy-somelye-web hack-v0.1.0

set -euo pipefail

API_IMAGE="${1:-}"
WEB_IMAGE="${2:-}"
TAG="${3:-}"

usage() {
    echo "Использование: $0 <api_image_ref> <web_image_ref> <tag>" >&2
    exit 2
}
[[ -z "$API_IMAGE" || -z "$WEB_IMAGE" || -z "$TAG" ]] && usage

BASE_DIR="/opt/somelye/hack"
COMPOSE_FILE="infra/compose.prod.yml"
ENV_FILE="${BASE_DIR}/.env"
RELEASES_DIR="${BASE_DIR}/releases"

log() { printf '[deploy-hack-yc] %s\n' "$*"; }
fail() { log "ОШИБКА: $*"; exit 1; }

[[ -d "$BASE_DIR" ]] || fail "нет ${BASE_DIR} — сначала первичная инициализация VM (infra/deploy/hack-yandex-cloud.md)"
[[ -f "${BASE_DIR}/${COMPOSE_FILE}" ]] || fail "нет ${BASE_DIR}/${COMPOSE_FILE} — сделать git clone/pull репозитория на VM"
[[ -f "$ENV_FILE" ]] || fail "нет ${ENV_FILE} — создать из infra/.env.prod.example (infra/deploy/hack-yandex-cloud.md, «Секреты на хосте»)"
command -v docker >/dev/null 2>&1 || fail "нет docker в PATH"

set_env_var() {
    # Тот же приём, что в deploy.sh: переписать файл целиком без старой строки + новая в конце
    # (без sed -i — разный синтаксис GNU/BSD).
    local key="$1" value="$2" file="$3" tmp
    tmp="$(mktemp)"
    if [[ -f "$file" ]]; then
        grep -vE "^${key}=" "$file" > "$tmp" || true
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv "$tmp" "$file"
}

mkdir -p "$RELEASES_DIR"
log "старт: api=${API_IMAGE}:${TAG} web=${WEB_IMAGE}:${TAG}"

# --- 1. Запомнить предыдущий тег (ручной откат, см. доку) -------------------------------
PREV_TAG="$(grep -E '^API_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
printf '%s' "${PREV_TAG:-}" > "${RELEASES_DIR}/.previous_tag"
log "предыдущий тег: ${PREV_TAG:-<нет — первый деплой>}"

# --- 2. Обновить .env новым образом/тегом api --------------------------------------------
set_env_var "API_IMAGE" "$API_IMAGE" "$ENV_FILE"
set_env_var "API_IMAGE_TAG" "$TAG" "$ENV_FILE"

# --- 3. Статика web: вытащить /dist из образа-носителя (infra/Dockerfile.web) -----------
log "docker pull ${WEB_IMAGE}:${TAG}"
docker pull "${WEB_IMAGE}:${TAG}"
WEB_CID="$(docker create "${WEB_IMAGE}:${TAG}")"
RELEASE_WEB_DIR="${RELEASES_DIR}/${TAG}/web"
rm -rf "$RELEASE_WEB_DIR"
mkdir -p "$RELEASE_WEB_DIR"
docker cp "${WEB_CID}:/dist/." "${RELEASE_WEB_DIR}/"
docker rm "$WEB_CID" >/dev/null
log "переключаю releases/current -> ${TAG}/web"
ln -sfn "${TAG}/web" "${RELEASES_DIR}/current"

# --- 4. Поднять контур — штатный compose.prod.yml, БЕЗ изменений ------------------------
cd "$BASE_DIR"
log "docker compose pull"
docker compose -p hack -f "$COMPOSE_FILE" --env-file .env pull
log "docker compose up -d"
docker compose -p hack -f "$COMPOSE_FILE" --env-file .env up -d --remove-orphans

# --- 5. Прогрев/health-check: тот же приём (20 попыток по 3с), что в deploy.sh и
#        publish-index.sh биз-линии — container_name см. примечание в шапке файла ----------
HEALTH_OK=0
STATUS="unknown"
for _ in $(seq 1 20); do
    STATUS="$(docker inspect --format '{{.State.Health.Status}}' somelye-prod-api 2>/dev/null || echo unknown)"
    if [[ "$STATUS" == "healthy" ]]; then
        HEALTH_OK=1
        break
    fi
    sleep 3
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
    fail "api не стал healthy вовремя (статус: ${STATUS}) — ручной откат: см. infra/deploy/hack-yandex-cloud.md, «Откат». Логи: docker compose -p hack -f ${COMPOSE_FILE} logs api"
fi

if curl -ks --max-time 5 https://127.0.0.1/nginx-health | grep -q ok; then
    log "edge-проверка nginx (127.0.0.1/nginx-health) — OK"
else
    log "ПРЕДУПРЕЖДЕНИЕ: nginx не ответил на /nginx-health (api при этом healthy) — проверить: docker compose -p hack -f ${COMPOSE_FILE} logs nginx"
fi

log "хак-деплой на tag=${TAG} завершён успешно"
