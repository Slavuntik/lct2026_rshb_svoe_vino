#!/usr/bin/env bash
# Доставка релиза на ams3 (локально или из CI): код → /opt/somelye/app, сборка веба →
# /opt/somelye/web, затем deploy.sh на сервере. Данные и модели сюда НЕ входят — их везёт
# sync-data.sh (разово и при обновлении индекса).
#
# Использование: push-release.sh <host> [путь к SSH-ключу CI]
# Перед запуском: cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build
set -euo pipefail
HOST="${1:?укажи хост ams3}"
KEY="${2:-$HOME/.ssh/ci_do_ams3}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH="ssh -i $KEY -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-%r@%h -o ControlPersist=120"

[ -f "$ROOT/apps/web/dist/index.html" ] || {
  echo "нет сборки веба — cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build"; exit 1; }

rsync -az --delete -e "$SSH" \
  --exclude '.git/' --exclude '.venv/' --exclude 'node_modules/' --exclude '__pycache__/' \
  --exclude '.pytest_cache/' --exclude '.DS_Store' \
  --exclude 'packages/cv/data/' --exclude 'packages/cv/devfix/' \
  --exclude 'packages/rag/data/' --exclude 'packages/rag/.fastembed_cache/' \
  --exclude 'apps/web/' --exclude 'apps/shell/' --exclude 'qa/' \
  --exclude 'pipeline/raw/' --exclude 'pipeline/raw-archive/' \
  "$ROOT/" "somelye@$HOST:/opt/somelye/app/"

rsync -az --delete -e "$SSH" "$ROOT/apps/web/dist/" "somelye@$HOST:/opt/somelye/web/"

$SSH "somelye@$HOST" 'bash /opt/somelye/app/infra/ams3/deploy.sh'
