#!/usr/bin/env bash
# Доставка релиза на ams3 (локально или из CI): код → /opt/somelye/app, сборка веба →
# /opt/somelye/web, затем deploy.sh на сервере.
#
# Код едет через `git archive` — то есть выкатывается РОВНО то, что закоммичено: незакоммиченные
# правки на сервер не попадают, а venv'ы, датасеты и кэши исключены самим git'ом. (Первая версия
# использовала rsync с --exclude и на macOS налетела на openrsync, который эти шаблоны игнорирует —
# уехало 1.4 ГБ локальных venv'ов.) Данные и модели сюда не входят — их везёт sync-data.sh.
#
# Использование: push-release.sh <host> [ключ]   ·  DEPLOY_REF=<ref> — выкатить другой коммит
#                PUSH_ONLY=1 — залить без рестарта. Перед запуском:
#                cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build
set -euo pipefail
HOST="${1:?укажи хост ams3}"
KEY="${2:-$HOME/.ssh/ci_do_ams3}"
REF="${DEPLOY_REF:-HEAD}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH="ssh -i $KEY -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-%r@%h -o ControlPersist=120"

[ -f "$ROOT/apps/web/dist/index.html" ] || {
  echo "нет сборки веба — cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build"; exit 1; }

echo "код ($(git -C "$ROOT" rev-parse --short "$REF")) →"
git -C "$ROOT" archive --format=tar "$REF" apps/api packages contracts infra pipeline/ref | gzip \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/app/* /opt/somelye/app/.[!.]* 2>/dev/null; mkdir -p /opt/somelye/app; tar xzf - -C /opt/somelye/app'

echo "веб →"
tar -czf - -C "$ROOT/apps/web/dist" . \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/web/* 2>/dev/null; mkdir -p /opt/somelye/web; tar xzf - -C /opt/somelye/web'

[ "${PUSH_ONLY:-0}" = 1 ] && { echo "код и веб залиты, deploy пропущен (PUSH_ONLY=1)"; exit 0; }
$SSH "somelye@$HOST" 'bash /opt/somelye/app/infra/ams3/deploy.sh'
