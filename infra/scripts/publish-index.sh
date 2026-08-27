#!/usr/bin/env bash
# infra/scripts/publish-index.sh — публикация индекса RAG на VPS (rsync + атомарный
# симлинк версии + рестарт api). Блокер ревью 02: артефакты `rag ingest` лежат в
# packages/rag/data/ (packages/rag/.gitignore — не едут ни через git, ни через
# .github/workflows/deploy.yml, который возит только api-образ и статику apps/web).
#
# ГДЕ ЗАПУСКАТЬ: с Mac Вячеслава (или CI-раннера с доступом по ssh к хосту) — В ОТЛИЧИЕ
# от backup.sh/restore.sh/deploy.sh, которые запускаются НА САМОМ VPS. Этот скрипт сам
# ходит на хост по ssh, локально ничего не поднимает.
#
# РАЗДЕЛЕНИЕ ОТВЕТСТВЕННОСТИ (см. reports/e-report.md): векторы Qdrant этим скриптом
# НЕ передаются. `rag ingest`, запущенный с QDRANT_URL=http://localhost:<туннель>
# (RUNBOOK §5, ssh -L на порт qdrant хоста), уже пишет векторы НАПРЯМУЮ в сетевой Qdrant.
# Этот скрипт публикует только ЛОКАЛЬНЫЕ сайдкар-файлы, которые Qdrant не хранит и
# которые нужны Retriever'у отдельно (проверено по коду — packages/rag/rag/config.py):
#   payloads/{wines,wineries,knowledge}.jsonl   — payload для BM25-фильтрации/resolve
#   bm25/{wines,wineries,knowledge}.pkl          — sparse-индекс (rank_bm25, в Qdrant нет)
#   labels.jsonl                                  — корпус для resolve_label
#   manifest.json                                 — версия, модели, counts (источник VERSION)
#
# Использование:
#   infra/scripts/publish-index.sh <prod|staging> [путь-к-data-каталогу]
#
# По умолчанию источник — packages/rag/data относительно корня репозитория (туда
# `rag ingest` пишет по умолчанию, packages/rag/rag/config.py: DATA_DIR). Версия берётся
# из <data>/manifest.json, поле "version" (пишет packages/rag/rag/ingest.py::run_ingest()) —
# отдельный номер версии аргументом не передаётся, чтобы нельзя было разойтись с тем,
# что реально лежит в манифесте.
#
# Требуемые переменные окружения (те же соглашения, что у deploy.yml — RUNBOOK «Секреты»):
#   DEPLOY_SSH_HOST (обязателен), DEPLOY_SSH_USER (по умолчанию deploy),
#   DEPLOY_SSH_PORT (по умолчанию 22).
#
# Пример (после rag ingest через ssh-туннель на staging, см. RUNBOOK §5):
#   DEPLOY_SSH_HOST=1.2.3.4 infra/scripts/publish-index.sh staging

set -euo pipefail

ENV="${1:-}"
SRC_DATA_DIR_ARG="${2:-}"

usage() {
    echo "Использование: $0 <prod|staging> [путь-к-data-каталогу]" >&2
    exit 2
}
[[ "$ENV" != "prod" && "$ENV" != "staging" ]] && usage

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DATA_DIR="${SRC_DATA_DIR_ARG:-${REPO_ROOT}/packages/rag/data}"

DEPLOY_SSH_HOST="${DEPLOY_SSH_HOST:?нужен: хост VPS (RUNBOOK «Секреты»)}"
DEPLOY_SSH_USER="${DEPLOY_SSH_USER:-deploy}"
DEPLOY_SSH_PORT="${DEPLOY_SSH_PORT:-22}"

BASE_REMOTE_DIR="/opt/somelye/${ENV}"
RAG_DATA_REMOTE_DIR="${BASE_REMOTE_DIR}/rag-data"

log() { printf '[publish-index:%s] %s\n' "$ENV" "$*"; }
fail() { log "ОШИБКА: $*"; exit 1; }
ssh_run() { ssh -p "$DEPLOY_SSH_PORT" "${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}" "$@"; }

command -v rsync >/dev/null 2>&1 || fail "нет rsync локально — см. RUNBOOK «Деплой индекса», запасной путь scp -r"
command -v python3 >/dev/null 2>&1 || fail "нет python3 локально — нужен, чтобы прочитать manifest.json"

# --- 0. Проверки источника (локально, до похода на хост) -----------------------------
[[ -d "$SRC_DATA_DIR" ]] || fail "нет каталога ${SRC_DATA_DIR} — сначала прогнать rag ingest"
MANIFEST="${SRC_DATA_DIR}/manifest.json"
[[ -f "$MANIFEST" ]] || fail "нет ${MANIFEST} — rag ingest не дописал манифест? прогнать заново"
for sub in payloads bm25 labels.jsonl; do
    [[ -e "${SRC_DATA_DIR}/${sub}" ]] || fail "нет ${SRC_DATA_DIR}/${sub} — индекс собран не полностью"
done

VERSION="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")"
[[ -n "$VERSION" ]] || fail "manifest.json без непустого поля version — прогнать rag ingest --version ..."
log "версия индекса к публикации: ${VERSION}"

# --- 1. Новый каталог версии на хосте (current трогаем позже, отдельным шагом) --------
REMOTE_VERSION_DIR="${RAG_DATA_REMOTE_DIR}/${VERSION}"
log "mkdir -p ${REMOTE_VERSION_DIR} на хосте"
ssh_run "mkdir -p '${REMOTE_VERSION_DIR}'"

log "rsync payloads/ bm25/ labels.jsonl manifest.json -> хост"
rsync -az --delete \
    -e "ssh -p ${DEPLOY_SSH_PORT}" \
    "${SRC_DATA_DIR}/payloads" \
    "${SRC_DATA_DIR}/bm25" \
    "${SRC_DATA_DIR}/labels.jsonl" \
    "${SRC_DATA_DIR}/manifest.json" \
    "${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST}:${REMOTE_VERSION_DIR}/"
# Нет rsync на клиенте/хосте? Запасной путь (медленнее, не инкрементальный):
#   tar -czf - -C "$SRC_DATA_DIR" payloads bm25 labels.jsonl manifest.json \
#     | ssh -p "$DEPLOY_SSH_PORT" "$DEPLOY_SSH_USER@$DEPLOY_SSH_HOST" \
#       "tar -xzf - -C '$REMOTE_VERSION_DIR'"

# --- 2. Проверка на хосте, что версия полная, ДО переключения symlink ------------------
ssh_run "test -s '${REMOTE_VERSION_DIR}/manifest.json' && \
         test -d '${REMOTE_VERSION_DIR}/payloads' && \
         test -d '${REMOTE_VERSION_DIR}/bm25' && \
         test -s '${REMOTE_VERSION_DIR}/labels.jsonl'" \
    || fail "новая версия на хосте неполная после rsync — current НЕ переключаю"

# --- 3. Запомнить предыдущую версию для отката -----------------------------------------
# current -> симлинк с ОТНОСИТЕЛЬНЫМ значением (само имя версии, см. шаг 4) — readlink
# отдаёт его как есть, без basename.
PREV_VERSION="$(ssh_run "readlink '${RAG_DATA_REMOTE_DIR}/current' 2>/dev/null" || true)"
log "предыдущая версия на хосте: ${PREV_VERSION:-<нет — первая публикация>}"

# --- 4. Атомарное переключение symlink --------------------------------------------------
# На самой первой публикации на хосте `current` может оказаться настоящей (пустой)
# ДИРЕКТОРИЕЙ, а не симлинком: Docker сам создаёт такую при первом `compose up -d`, если
# bind-mount источника (compose.*.yml: rag-data/current:/data/rag:ro) указывает на путь,
# которого ещё не существовало (RUNBOOK §2.6). `ln -sfn` директорию симлинком не заменит —
# разруливаем здесь, а не полагаемся на то, что оператор запустит этот скрипт раньше
# первого `up -d`.
ssh_run "if [ -d '${RAG_DATA_REMOTE_DIR}/current' ] && [ ! -L '${RAG_DATA_REMOTE_DIR}/current' ]; then \
    rmdir '${RAG_DATA_REMOTE_DIR}/current' || { echo 'current — непустая директория (не симлинк), разобраться руками на хосте' >&2; exit 1; }; \
fi"
log "переключаю ${RAG_DATA_REMOTE_DIR}/current -> ${VERSION}"
ssh_run "ln -sfn '${VERSION}' '${RAG_DATA_REMOTE_DIR}/current'"

# --- 5. Рестарт api — RAG_DATA_DIR читается один раз при старте процесса ----------------
# (apps/api/app/main.py::create_app() -> get_retriever(settings) вызывается один раз;
# новые файлы под уже смонтированным /data/rag процесс сам не перечитает без рестарта).
log "docker compose restart api"
ssh_run "cd '${BASE_REMOTE_DIR}' && docker compose -p ${ENV} -f infra/compose.${ENV}.yml --env-file .env restart api"

# --- 6. Health-check после рестарта ------------------------------------------------------
HEALTH_OK=0
STATUS="unknown"
for _ in $(seq 1 20); do
    STATUS="$(ssh_run "docker inspect --format '{{.State.Health.Status}}' somelye-${ENV}-api 2>/dev/null" || echo unknown)"
    if [[ "$STATUS" == "healthy" ]]; then
        HEALTH_OK=1
        break
    fi
    sleep 3
done

if [[ "$HEALTH_OK" -ne 1 ]]; then
    log "api не стал healthy после рестарта (статус: ${STATUS}) — откатываю симлинк"
    if [[ -n "$PREV_VERSION" ]]; then
        ssh_run "ln -sfn '${PREV_VERSION}' '${RAG_DATA_REMOTE_DIR}/current'"
        ssh_run "cd '${BASE_REMOTE_DIR}' && docker compose -p ${ENV} -f infra/compose.${ENV}.yml --env-file .env restart api"
        fail "откат на индекс ${PREV_VERSION} выполнен. Смотреть: ssh ${DEPLOY_SSH_USER}@${DEPLOY_SSH_HOST} docker compose -p ${ENV} -f ${BASE_REMOTE_DIR}/infra/compose.${ENV}.yml logs api"
    fi
    fail "api не healthy, а предыдущей версии индекса для отката нет (первая публикация?). Смотреть логи api руками."
fi

log "индекс ${VERSION} опубликован и активен на ${ENV}."

# --- 7. Уборка старых версий на хосте (оставляем последние 5) --------------------------
ssh_run "cd '${RAG_DATA_REMOTE_DIR}' && ls -1t | grep -vE '^current$' | tail -n +6 | xargs -r rm -rf --" \
    || log "ПРЕДУПРЕЖДЕНИЕ: не удалось почистить старые версии индекса на хосте (не критично)"
