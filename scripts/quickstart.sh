#!/usr/bin/env bash
# scripts/quickstart.sh — «Свой Сомелье»: локальный запуск одной командой (кейс №10 ЛЦТ 2026).
#
# Режимы:
#   fast (по умолчанию) — реальный поиск сомелье (индекс уже в репозитории) + сканер и
#                          LLM-заглушки. Поднимается за минуты, без скачивания моделей
#                          сканера и без данных кейса.
#   full                 — настоящее распознавание этикеток. Требует CASE_DATA_DIR (свои
#                          материалы кейса), качает модели (несколько ГБ) и строит индекс
#                          сканера — это десятки минут, не "пара минут".
#   verify               — прогон официального eval/participant_test.sh против локального API.
#   status               — что сейчас поднято.
#   stop                 — остановить всё, что подняла эта команда.
#
# Подробности, формат данных и разбор проблем — docs/QUICKSTART.md.
# Совместимость: bash (macOS системный 3.2 и Linux 5.x), без ассоциативных массивов
# и прочих bash4+-измов — сознательно, см. docs/QUICKSTART.md "Как это сделано".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT/.local/quickstart"
LOG_DIR="$RUN_DIR/logs"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
API_HOST="127.0.0.1"
ASSUME_YES=0
NO_WEB=0

DEFAULT_UPLOADS_REL="prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"
CV_MODEL_FULL="google/siglip2-base-patch16-384"

# Массивы объявлены заранее и всегда заполняются целиком перед использованием —
# на bash 3.2 (macOS) `"${arr[@]}"` пустого массива под `set -u` падает с
# "unbound variable" (проверено эмпирически), поэтому это не просто стиль.
API_ENV=()
WEB_ENV=()
REFS_ARGS=()
REFS_DESC=""
EXTRA_ARGS=()

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------- вывод ----
say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s ==\n' "$*"; }
warn() { printf 'ВНИМАНИЕ: %s\n' "$*" >&2; }
die()  { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Использование: scripts/quickstart.sh <команда> [опции]

Команды:
  fast              Быстрый режим (по умолчанию): реальный поиск сомелье, сканер
                     и LLM — заглушки. Минуты, без скачиваний.
  full              Полный режим: настоящее распознавание. Нужен CASE_DATA_DIR,
                     качает модели, строит индекс — десятки минут.
  verify            Прогон официального eval/participant_test.sh против локального API.
                     Доп. опции: --images-dir DIR --manifest FILE --endpoint URL --output FILE
  status            Что сейчас поднято (порты, PID, healthz).
  stop              Остановить всё, что подняла эта команда.
  help              Эта справка.

Опции:
  -y, --yes         Не спрашивать подтверждения перед тяжёлыми шагами (full).
  --no-web          Не поднимать веб-клиент, только API.

Переменные окружения:
  API_PORT          Порт API, по умолчанию 8000.
  WEB_PORT          Порт веб-клиента, по умолчанию 5173.
  CASE_DATA_DIR     Путь к своим материалам кейса — только для full.

Примеры:
  scripts/quickstart.sh
  API_PORT=8001 WEB_PORT=5174 scripts/quickstart.sh fast
  CASE_DATA_DIR=/путь/к/данным scripts/quickstart.sh full --yes
  scripts/quickstart.sh verify --images-dir ./my-photos --manifest ./my-photos/manifest.tsv

Документация: docs/QUICKSTART.md
EOF
}

# ------------------------------------------------------------ проверки ----
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "$2"; }

check_uv() {
  require_cmd uv "uv не найден. Установите: curl -LsSf https://astral.sh/uv/install.sh | sh (подробнее: https://docs.astral.sh/uv/getting-started/installation/)"
  say "uv: $(uv --version)"
}

check_python() {
  if uv python find 3.12 >/dev/null 2>&1; then
    say "python 3.12: найден ($(uv python find 3.12))"
  else
    warn "python 3.12 локально не найден — uv скачает подходящую сборку сам при первом 'uv sync' (~40 МБ, один раз, нужна сеть)."
  fi
}

check_node() {
  require_cmd node "Node.js не найден. Нужен Node.js >= 22: https://nodejs.org/en/download (или: nvm install 22)"
  local v major
  v="$(node --version)"
  major="${v#v}"; major="${major%%.*}"
  case "$major" in
    ''|*[!0-9]*) die "не смог разобрать версию node ($v) — нужен Node.js >= 22" ;;
  esac
  [ "$major" -ge 22 ] || die "найден node $v — нужен Node.js >= 22. Обновите: https://nodejs.org/en/download (или: nvm install 22)"
  say "node: $v"
  require_cmd npm "npm не найден (обычно ставится вместе с Node.js)"
}

check_verify_tools() {
  require_cmd curl "curl не найден — нужен команде verify"
  require_cmd jq "jq не найден — нужен официальному participant_test.sh (команда verify). macOS: brew install jq; Linux: apt/yum install jq"
  require_cmd awk "awk не найден — нужен официальному participant_test.sh (команда verify)"
}

# --------------------------------------------------------------- порты ----
port_busy() {
  # Проверяем и IPv4-, и IPv6-loopback: живая проверка на общей машине поймала
  # случай, когда чужой vite слушал только `::1` (IPv6), а наш `--host 127.0.0.1`
  # спокойно забиндился рядом на IPv4 того же номера порта — проверка только по
  # 127.0.0.1 такое соседство не замечала (см. reports/devops-quickstart.md).
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
  (exec 3<>"/dev/tcp/::1/$1") 2>/dev/null && return 0
  return 1
}

port_owner_hint() {
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -n -P -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2{printf " (похоже, это %s, pid %s)", $1, $2}'
}

require_free_port() {
  local port="$1" what="$2" hint
  if port_busy "$port"; then
    hint="$(port_owner_hint "$port")"
    die "порт $port уже занят${hint} — он нужен для $what. Освободите его или укажите другой порт через переменные окружения API_PORT/WEB_PORT (см. --help)."
  fi
}

wait_healthz() {
  local port="$1" timeout_s="$2" start now body
  start=$(date +%s)
  while true; do
    if body=$(curl -fsS "http://127.0.0.1:${port}/v1/healthz" 2>/dev/null); then
      case "$body" in *'"warm":true'*) printf '%s' "$body"; return 0 ;; esac
    fi
    now=$(date +%s)
    [ $(( now - start )) -lt "$timeout_s" ] || return 1
    sleep 1
  done
}

wait_http_ok() {
  local host="$1" port="$2" timeout_s="$3" start now
  start=$(date +%s)
  while true; do
    curl -fsS -o /dev/null "http://$host:$port/" 2>/dev/null && return 0
    now=$(date +%s)
    [ $(( now - start )) -lt "$timeout_s" ] || return 1
    sleep 1
  done
}

confirm_or_exit() {
  [ "$ASSUME_YES" = 1 ] && return 0
  local reply=""
  printf '%s [y/N] ' "$1"
  if ! read -r reply < /dev/tty 2>/dev/null; then
    die "нет интерактивного терминала для подтверждения — перезапустите с флагом --yes"
  fi
  case "$reply" in
    y|Y|yes|YES|Yes) return 0 ;;
    *) die "отменено" ;;
  esac
}

# --------------------------------------------------------- процессы -------
pid_file() { printf '%s/%s.pid' "$RUN_DIR" "$1"; }

is_running() {
  local pf; pf="$(pid_file "$1")"
  [ -f "$pf" ] && kill -0 "$(cat "$pf")" 2>/dev/null
}

start_api() {
  local log="$LOG_DIR/api.log"
  : > "$log"
  ( cd "$ROOT/apps/api" && exec env "${API_ENV[@]}" .venv/bin/uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" ) >"$log" 2>&1 &
  echo $! > "$(pid_file api)"
  disown 2>/dev/null || true
}

ensure_web_deps() {
  if [ ! -d "$ROOT/apps/web/node_modules" ]; then
    say "ставлю npm-зависимости веб-клиента (один раз)…"
    ( cd "$ROOT/apps/web" && npm ci )
  fi
  [ -x "$ROOT/apps/web/node_modules/.bin/vite" ] || die "vite не найден в apps/web/node_modules/.bin — смотрите вывод npm ci выше"
}

start_web() {
  local log="$LOG_DIR/web.log"
  : > "$log"
  ( cd "$ROOT/apps/web" && exec env "${WEB_ENV[@]}" ./node_modules/.bin/vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort ) >"$log" 2>&1 &
  echo $! > "$(pid_file web)"
  disown 2>/dev/null || true
}

stop_one() {
  local name="$1" pf pid i
  pf="$(pid_file "$name")"
  if [ ! -f "$pf" ]; then
    say "$name: не запущен (нет $pf)"
    return 0
  fi
  pid="$(cat "$pf")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    i=0
    while [ "$i" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do sleep 0.5; i=$((i+1)); done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    say "$name: остановлен (pid $pid)"
  else
    say "$name: pid $pid из файла уже не отвечает"
  fi
  rm -f "$pf"
}

cmd_stop() { stop_one web; stop_one api; }

cmd_status() {
  local any=0
  if is_running api; then
    any=1
    say "api: запущен (pid $(cat "$(pid_file api)")), порт $API_PORT"
    curl -fsS "http://127.0.0.1:${API_PORT}/v1/healthz" 2>/dev/null && printf '\n' || say "  healthz не отвечает"
  else
    say "api: не запущен"
  fi
  if is_running web; then
    any=1
    say "web: запущен (pid $(cat "$(pid_file web)")), порт $WEB_PORT — http://127.0.0.1:$WEB_PORT/"
  else
    say "web: не запущен"
  fi
  [ "$any" = 1 ] || say "(ничего не поднято этим скриптом — нет файлов $RUN_DIR/*.pid)"
}

print_summary() {
  step "Готово ($1)"
  if [ "$NO_WEB" = 0 ]; then
    say "Веб:       http://127.0.0.1:$WEB_PORT/"
  else
    say "Веб:       не поднят (--no-web)"
  fi
  say "Swagger:   http://127.0.0.1:$API_PORT/v1/docs"
  say "Метрики:   http://127.0.0.1:$API_PORT/v1/metrics/scan"
  say "Healthz:   http://127.0.0.1:$API_PORT/v1/healthz"
  say ""
  say "Проверка официальным скриптом:  scripts/quickstart.sh verify"
  say "Статус:                         scripts/quickstart.sh status"
  say "Остановить:                     scripts/quickstart.sh stop"
  if [ "$NO_WEB" = 0 ]; then
    say "Логи:                           $LOG_DIR/api.log , $LOG_DIR/web.log"
  else
    say "Логи:                           $LOG_DIR/api.log"
  fi
}

# ------------------------------------------------------------- fast -------
cmd_fast() {
  step "Проверка версий"
  check_uv; check_python; check_node

  step "Порты"
  require_free_port "$API_PORT" "API"
  [ "$NO_WEB" = 1 ] || require_free_port "$WEB_PORT" "веб-клиента"

  step "Зависимости API (быстрый режим — без сканера, без torch)"
  # --inexact: ДОБАВИТЬ недостающее, не ТРОГАТЬ лишнее. Обычный `uv sync --frozen`
  # синхронизирует окружение ТОЧНО под базовый набор — если venv уже содержит
  # больше (например, кто-то уже ставил --extra integration), он их снесёт.
  # Проверено вживую (см. reports/devops-quickstart.md, "Риски/находки"): именно
  # так один прогон fast-режима в существующем dev-окружении удалил 94 пакета
  # (torch/transformers/paddleocr/...), а собранные заново по кускам (несколько
  # разных `uv sync` подряд) опенсv-пакеты пересеклись файлами и сломали импорт
  # cv2 — почти на час заблокировав приёмочные прогоны команды. --inexact не
  # трогает то, что уже стоит, поэтому на чистом клоне ведёт себя так же, а на
  # уже подготовленной машине — безопасно.
  ( cd "$ROOT/apps/api" && uv sync --frozen --inexact )
  say "добавляю реальный поиск сомелье (packages/rag — лёгкий пакет, без torch)…"
  ( cd "$ROOT/apps/api" && uv pip install --python "$ROOT/apps/api/.venv/bin/python" -q -e ../../packages/rag )
  say ""
  say "Индекс сомелье уже в репозитории (packages/rag/data, ничего качать не надо)."
  say "Модель эмбеддинга запроса (~250 МБ, sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
  say "через fastembed) скачается при первом запросе, если её ещё нет в кэше — один раз."
  say "Реранкер поиска (ещё ~1.1 ГБ) в быстром режиме намеренно отключён — см. docs/QUICKSTART.md."

  step "Запуск API (:$API_PORT) — сомелье реальный, сканер и LLM — заглушки"
  API_ENV=(
    "RAG_PROVIDER=real" "RAG_MODE=embedded" "RAG_RERANKER_MODEL="
    "IMAGE_PROVIDER=mock" "VERIFIER_PROVIDER=mock" "LLM_PROVIDER=mock"
    "DATABASE_URL=sqlite:///$RUN_DIR/fast.db"
    "CORS_ORIGINS=http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT"
  )
  start_api
  say "лог: $LOG_DIR/api.log"
  say "жду готовности (healthz warm:true)…"
  hz_body="$(wait_healthz "$API_PORT" 180 2>/dev/null)" && hz_ok=1 || hz_ok=0
  [ "$hz_ok" = 1 ] || die "API не поднялся за 180 с — смотрите $LOG_DIR/api.log"
  say "готово: $hz_body"

  if [ "$NO_WEB" = 0 ]; then
    step "Запуск веб-клиента (:$WEB_PORT)"
    ensure_web_deps
    WEB_ENV=("VITE_API_MODE=real" "VITE_THEME=portal" "VINCHIK_API_URL=http://127.0.0.1:$API_PORT")
    start_web
    say "лог: $LOG_DIR/web.log"
    wait_http_ok 127.0.0.1 "$WEB_PORT" 60 || warn "веб не ответил за 60 с на :$WEB_PORT — проверьте $LOG_DIR/web.log"
  fi

  print_summary fast
}

# ------------------------------------------------------------- full -------
full_detect_refs() {
  if [ -n "${CASE_SLUG_REFS_JSON:-}" ]; then
    [ -f "$CASE_SLUG_REFS_JSON" ] || die "CASE_SLUG_REFS_JSON=$CASE_SLUG_REFS_JSON — файла нет"
    local uploads="${CASE_UPLOADS_DIR:-$CASE_DATA_DIR/$DEFAULT_UPLOADS_REL}"
    [ -d "$uploads" ] || die "не нашёл папку с фото '$uploads' — укажите CASE_UPLOADS_DIR=/путь/к/фото"
    REFS_ARGS=(--refs-json "$CASE_SLUG_REFS_JSON" --uploads-dir "$uploads"); REFS_DESC="$CASE_SLUG_REFS_JSON"
    return 0
  fi
  if [ -n "${CASE_REFS_CSV:-}" ]; then
    [ -f "$CASE_REFS_CSV" ] || die "CASE_REFS_CSV=$CASE_REFS_CSV — файла нет"
    REFS_ARGS=(--refs "$CASE_REFS_CSV"); REFS_DESC="$CASE_REFS_CSV"
    return 0
  fi
  if [ -n "${CASE_REFS_DIR:-}" ]; then
    [ -d "$CASE_REFS_DIR" ] || die "CASE_REFS_DIR=$CASE_REFS_DIR — папки нет"
    REFS_ARGS=(--refs "$CASE_REFS_DIR"); REFS_DESC="$CASE_REFS_DIR"
    return 0
  fi
  if [ -f "$CASE_DATA_DIR/slug_refs.json" ] && [ -d "$CASE_DATA_DIR/$DEFAULT_UPLOADS_REL" ]; then
    REFS_ARGS=(--refs-json "$CASE_DATA_DIR/slug_refs.json" --uploads-dir "$CASE_DATA_DIR/$DEFAULT_UPLOADS_REL")
    REFS_DESC="$CASE_DATA_DIR/slug_refs.json"
    return 0
  fi
  if [ -f "$CASE_DATA_DIR/strapi_output0709.csv" ]; then
    REFS_ARGS=(--refs "$CASE_DATA_DIR/strapi_output0709.csv"); REFS_DESC="$CASE_DATA_DIR/strapi_output0709.csv"
    return 0
  fi
  if [ -d "$CASE_DATA_DIR/refs" ]; then
    REFS_ARGS=(--refs "$CASE_DATA_DIR/refs"); REFS_DESC="$CASE_DATA_DIR/refs"
    return 0
  fi
  return 1
}

cmd_full() {
  step "Проверка версий"
  check_uv; check_python; check_node

  step "Данные кейса"
  if [ -z "${CASE_DATA_DIR:-}" ]; then
    die "не задана переменная CASE_DATA_DIR — путь к вашим материалам кейса (дамп каталога + фото). Пример: CASE_DATA_DIR=/путь/к/данным scripts/quickstart.sh full. Формат — docs/QUICKSTART.md, раздел «Полный режим»."
  fi
  [ -d "$CASE_DATA_DIR" ] || die "CASE_DATA_DIR=$CASE_DATA_DIR — такой папки нет"

  if ! full_detect_refs; then
    die "в CASE_DATA_DIR='$CASE_DATA_DIR' не нашёл узнаваемого набора эталонных фото. Ожидается один из вариантов (docs/QUICKSTART.md, «Формат каталога»): (1) slug_refs.json + папка $DEFAULT_UPLOADS_REL/; (2) CSV с колонками slug,image_path — путь через CASE_REFS_CSV=...; (3) папка фото, где имя файла без расширения = слаг — путь через CASE_REFS_DIR=..."
  fi
  say "источник эталонов: $REFS_DESC"

  step "Порты"
  require_free_port "$API_PORT" "API"
  [ "$NO_WEB" = 1 ] || require_free_port "$WEB_PORT" "веб-клиента"

  step "План — честно, без «пары минут»"
  cat <<PLAN
1) Python ML-зависимости (torch, transformers, PaddleOCR, RapidOCR) — на нашей
   машине это ~1.8 ГБ на диске; качается один раз, время зависит от интернета.
2) Веса модели изображений $CV_MODEL_FULL — ~1.4 ГБ, с Hugging Face.
3) Модели OCR (RapidOCR/PaddleOCR) — около 100 МБ, скачаются при первом сканировании.
4) Сборка индекса сканера по вашему каталогу — САМЫЙ ДОЛГИЙ шаг: на нашей машине
   (Apple Silicon, ускоритель MPS) сборка индекса на ~2050 позиций заняла ~29 минут
   (22 мин рендер ракурсов + 6.5 мин эмбеддинги, source: reports/g3-real-index.md,
   packages/cv/data/build_summary_*.json). На процессоре без ускорителя (наш прод-сервер,
   4 vCPU) — около 45 минут на тот же объём. Время растёт примерно линейно с числом
   позиций в вашем каталоге.
Итого от команды до готового API — реалистично от 40 минут до полутора часов,
в основном зависит от скорости интернета и числа ядер/наличия GPU-ускорителя.
Ключей LLM в репозитории нет и не будет — чат остаётся на детерминированной
заглушке (LLM_PROVIDER=mock) даже в полном режиме, см. docs/QUICKSTART.md.
PLAN
  confirm_or_exit "Продолжить?"

  step "Python-зависимости (тяжёлые — torch/transformers/PaddleOCR/RapidOCR)"
  # --inexact — та же причина, что в cmd_fast выше: не трогать то, что уже стоит
  # в этом окружении (dev-зависимости, другие extras).
  ( cd "$ROOT/apps/api" && uv sync --frozen --extra integration --inexact )

  step "Сборка индекса сканера из вашего каталога (см. план выше)"
  CV_DATA_DIR_FULL="$ROOT/packages/cv/data"
  mkdir -p "$CV_DATA_DIR_FULL"
  say "энкодер: $CV_MODEL_FULL (веса скачаются сейчас, если их ещё нет в кэше Hugging Face)"
  (
    cd "$ROOT/apps/api" \
    && env HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 \
           CV_DATA_DIR="$CV_DATA_DIR_FULL" CV_MODEL="$CV_MODEL_FULL" \
           .venv/bin/cv build-index "${REFS_ARGS[@]}" --version "quickstart-$(date +%Y%m%d-%H%M%S)"
  )

  step "Карточки каталога для вин вне поиска сомелье (необязательно)"
  if [ -f "$CASE_DATA_DIR/strapi_output0709.csv" ]; then
    if ( cd "$ROOT/apps/api" && env CASE_DATA_DIR="$CASE_DATA_DIR" .venv/bin/python scripts/build_case_catalog.py ); then
      say "case_catalog.json собран"
    else
      warn "build_case_catalog.py не отработал — не критично, поиск сканера это не затрагивает"
    fi
    if ( cd "$ROOT/apps/api" && env CASE_DATA_DIR="$CASE_DATA_DIR" .venv/bin/python scripts/build_case_thumbs.py ); then
      say "превью thumbs/ собраны"
    else
      warn "build_case_thumbs.py не отработал — карточки будут без превью, не критично"
    fi
  else
    say "strapi_output0709.csv не найден в CASE_DATA_DIR — пропускаю (нужен только для превью вин вне поиска сомелье)"
  fi

  step "Запуск API (:$API_PORT) — распознавание настоящее"
  API_ENV=(
    "RAG_PROVIDER=real" "RAG_MODE=embedded"
    "IMAGE_PROVIDER=real" "VERIFIER_PROVIDER=real" "LLM_PROVIDER=mock"
    "CV_DATA_DIR=$CV_DATA_DIR_FULL" "CV_MODEL=$CV_MODEL_FULL"
    "CASE_DATA_DIR=$CASE_DATA_DIR"
    "DATABASE_URL=sqlite:///$RUN_DIR/full.db"
    "CORS_ORIGINS=http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT"
  )
  start_api
  say "лог: $LOG_DIR/api.log"
  say "жду готовности — первый прогрев настоящих моделей может занять до нескольких минут…"
  hz_body="$(wait_healthz "$API_PORT" 600 2>/dev/null)" && hz_ok=1 || hz_ok=0
  [ "$hz_ok" = 1 ] || die "API не поднялся за 600 с — смотрите $LOG_DIR/api.log"
  say "готово: $hz_body"

  if [ "$NO_WEB" = 0 ]; then
    step "Запуск веб-клиента (:$WEB_PORT)"
    ensure_web_deps
    WEB_ENV=("VITE_API_MODE=real" "VITE_THEME=portal" "VINCHIK_API_URL=http://127.0.0.1:$API_PORT")
    start_web
    wait_http_ok 127.0.0.1 "$WEB_PORT" 60 || warn "веб не ответил за 60 с — проверьте $LOG_DIR/web.log"
  fi

  print_summary full
  say ""
  say "/v1/metrics/scan будет пустым, пока вы не прогоните verify — правильные ответы"
  say "есть только у организаторов, мы их не имеем и не эмулируем."
}

# ------------------------------------------------------------ verify ------
cmd_verify() {
  check_verify_tools
  local images_dir="$ROOT/eval/queries"
  local manifest="$ROOT/eval/queries.tsv"
  local endpoint="http://127.0.0.1:${API_PORT}/v1/eval/predict"
  local output="$RUN_DIR/predictions-$(date +%Y%m%d-%H%M%S).jsonl"

  while [ $# -gt 0 ]; do
    case "$1" in
      --images-dir) [ $# -ge 2 ] || die "--images-dir требует значение"; images_dir="$2"; shift 2 ;;
      --manifest)   [ $# -ge 2 ] || die "--manifest требует значение"; manifest="$2"; shift 2 ;;
      --endpoint)   [ $# -ge 2 ] || die "--endpoint требует значение"; endpoint="$2"; shift 2 ;;
      --output)     [ $# -ge 2 ] || die "--output требует значение"; output="$2"; shift 2 ;;
      *) die "неизвестный аргумент verify: $1 (см. scripts/quickstart.sh help)" ;;
    esac
  done

  if [ ! -d "$images_dir" ] || [ -z "$(ls -A "$images_dir" 2>/dev/null | grep -v '^\.gitignore$' || true)" ]; then
    die "папка с фото для проверки пуста или не найдена: $images_dir
В репозитории лежат только имена файлов образца — сами фото организаторов мы не публикуем.
Передайте свои фото явно: scripts/quickstart.sh verify --images-dir /путь/к/фото --manifest /путь/к/manifest.tsv
Формат manifest — TSV с колонками query_id и image_path, см. eval/README.md."
  fi
  [ -f "$manifest" ] || die "манифест не найден: $manifest"

  if ! curl -fsS -o /dev/null "http://127.0.0.1:${API_PORT}/v1/healthz" 2>/dev/null; then
    die "API на порту $API_PORT не отвечает — сначала запустите scripts/quickstart.sh fast (или full)"
  fi

  step "Прогон официального eval/participant_test.sh"
  say "images-dir: $images_dir"
  say "manifest:   $manifest"
  say "endpoint:   $endpoint"
  say "output:     $output"
  bash "$ROOT/eval/participant_test.sh" --images-dir "$images_dir" --manifest "$manifest" \
    --endpoint "$endpoint" --output "$output"
  say ""
  say "Готово: $output"
  say "Правильные ответы и итоговую точность считают организаторы — передайте им"
  say "этот файл, как написано в eval/README.md."
}

# ---------------------------------------------------------- разбор argv ---
# COMMAND нарочно стартует ПУСТЫМ, а не сразу "fast": иначе нераспознанный
# аргумент молча провалился бы в EXTRA_ARGS, а команда осталась бы дефолтной
# "fast" без единого предупреждения — именно так это и было устроено до
# живой проверки на этом Mac, откуда и появилась данная защита (см. reports/
# devops-quickstart.md, "Риски/находки" — команда `bogus` реально подняла
# fast-режим и переустановила apps/api/.venv, прежде чем это заметили).
COMMAND=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) COMMAND="help"; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --no-web) NO_WEB=1; shift ;;
    fast|full|verify|status|stop|help)
      [ -z "$COMMAND" ] || die "команда уже указана как '$COMMAND' — лишний аргумент: $1"
      COMMAND="$1"; shift
      ;;
    *)
      if [ "$COMMAND" = "verify" ]; then
        EXTRA_ARGS+=("$1"); shift
      else
        die "неизвестный аргумент: $1 (см. scripts/quickstart.sh help)"
      fi
      ;;
  esac
done
[ -n "$COMMAND" ] || COMMAND="fast"

case "$COMMAND" in
  fast) cmd_fast ;;
  full) cmd_full ;;
  verify)
    if [ "${#EXTRA_ARGS[@]}" -gt 0 ]; then cmd_verify "${EXTRA_ARGS[@]}"; else cmd_verify; fi
    ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  help) usage ;;
  *) usage; exit 1 ;;
esac
