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
git -C "$ROOT" archive --format=tar "$REF" apps/api packages contracts infra pipeline/ref pipeline/catalog pipeline/build | gzip \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/app/* /opt/somelye/app/.[!.]* 2>/dev/null; mkdir -p /opt/somelye/app; tar xzf - -C /opt/somelye/app'

echo "веб →"
tar -czf - -C "$ROOT/apps/web/dist" . \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/web/* 2>/dev/null; mkdir -p /opt/somelye/web; tar xzf - -C /opt/somelye/web'

# Ключ GigaChat из секрета GitHub (GIGACHAT_AUTH_KEY) — едет через stdin, а не аргументом:
# так его не видно ни в списке процессов, ни в командной строке на сервере. Нет секрета —
# конфиг сервера не трогаем (остаётся то, что там уже стоит, например LLM_PROVIDER=mock).
# Шлюз VLM (чтение этикетки для слияния CV+текст): адрес и ключ — секреты GitHub VISION_LLM_URL и
# VISION_LLM_KEY. Оба едут через stdin (ни в аргументах, ни в репозитории: репозиторий станет
# публичным, а адрес — наш GPU-сервер). TLS проверяется штатно (сертификат Let's Encrypt на IP).
if [ -n "${VISION_LLM_KEY:-}" ] && [ -n "${VISION_LLM_URL:-}" ]; then
  printf '%s\n%s\n' "$VISION_LLM_URL" "$VISION_LLM_KEY" | $SSH "somelye@$HOST" \
    'read -r u; read -r k; f=/opt/somelye/somelye.env; sed -i "/^VISION_LLM_URL=/d; /^VISION_LLM_KEY=/d" "$f"; printf "VISION_LLM_URL=%s\nVISION_LLM_KEY=%s\n" "$u" "$k" >> "$f"'
fi
if [ -n "${GIGACHAT_AUTH_KEY:-}" ]; then
  printf '%s\n' "$GIGACHAT_AUTH_KEY" | $SSH "somelye@$HOST" \
    'read -r k; f=/opt/somelye/somelye.env; sed -i "/^GIGACHAT_AUTH_KEY=/d; /^LLM_PROVIDER=/d" "$f"; printf "LLM_PROVIDER=gigachat\nGIGACHAT_AUTH_KEY=%s\n" "$k" >> "$f"'
  echo "ключ GigaChat из секрета установлен на сервере (LLM_PROVIDER=gigachat)"
fi

[ "${PUSH_ONLY:-0}" = 1 ] && { echo "код и веб залиты, deploy пропущен (PUSH_ONLY=1)"; exit 0; }
$SSH "somelye@$HOST" 'bash /opt/somelye/app/infra/ams3/deploy.sh'
