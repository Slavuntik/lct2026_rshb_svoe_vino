#!/usr/bin/env bash
# Доставка релиза на второй стенд (cpulab, IP-адрес по умолчанию ниже — задание 27.09):
# код → /opt/somelye/app, сборка веба → /opt/somelye/web, затем deploy.sh на сервере.
# Копия infra/ams3/push-release.sh с другим дефолтным хостом/ключом и путём deploy.sh —
# см. README «Как не хардкодить адрес», это ЕДИНСТВЕННОЕ структурное отличие от ams3.
#
# Код едет через `git archive` — выкатывается РОВНО закоммиченное. Данные/модели сюда не
# входят — их везёт sync-data.sh.
#
# Использование: push-release.sh [host] [ключ]   ·  DEPLOY_REF=<ref> — выкатить другой коммит
#                PUSH_ONLY=1 — залить без рестарта. Перед запуском:
#                cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build
set -euo pipefail
HOST="${1:-46.243.211.32}"
KEY="${2:-$HOME/.ssh/cpu_lab}"
REF="${DEPLOY_REF:-HEAD}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH="ssh -i $KEY -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-cpulab-%r@%h -o ControlPersist=120"

[ -f "$ROOT/apps/web/dist/index.html" ] || {
  echo "нет сборки веба — cd apps/web && VITE_API_MODE=real VITE_THEME=portal npm run build"; exit 1; }

echo "код ($(git -C "$ROOT" rev-parse --short "$REF")) →"
git -C "$ROOT" archive --format=tar "$REF" apps/api packages contracts infra pipeline/ref pipeline/catalog pipeline/build | gzip \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/app/* /opt/somelye/app/.[!.]* 2>/dev/null; mkdir -p /opt/somelye/app; tar xzf - -C /opt/somelye/app'

echo "веб →"
tar -czf - -C "$ROOT/apps/web/dist" . \
  | $SSH "somelye@$HOST" 'rm -rf /opt/somelye/web/* 2>/dev/null; mkdir -p /opt/somelye/web; tar xzf - -C /opt/somelye/web'

# Секреты (VISION_LLM_*/GigaChat) — тот же принцип, что infra/ams3/push-release.sh: через
# stdin, никогда аргументом/в репозитории. Для cpulab секреты обычно переносятся ОДИН раз
# с ams3 (README, «Секреты» — прямой поток ams3 -> cpulab, без участия этого скрипта);
# переменные ниже остаются на случай РОТАЦИИ ключа именно на этом стенде без похода на ams3.
if [ -n "${VISION_LLM_KEY:-}" ] && [ -n "${VISION_LLM_URL:-}" ]; then
  printf '%s\n%s\n' "$VISION_LLM_URL" "$VISION_LLM_KEY" | $SSH "somelye@$HOST" \
    'read -r u; read -r k; f=/opt/somelye/somelye.env; sed -i "/^VISION_LLM_URL=/d; /^VISION_LLM_KEY=/d" "$f"; printf "VISION_LLM_URL=%s\nVISION_LLM_KEY=%s\n" "$u" "$k" >> "$f"'
fi
if [ -n "${GIGACHAT_AUTH_KEY:-}" ]; then
  printf '%s\n' "$GIGACHAT_AUTH_KEY" | $SSH "somelye@$HOST" \
    'read -r k; f=/opt/somelye/somelye.env; sed -i "/^GIGACHAT_AUTH_KEY=/d; /^LLM_PROVIDER=/d" "$f"; printf "LLM_PROVIDER=gigachat\nGIGACHAT_AUTH_KEY=%s\n" "$k" >> "$f"'
  echo "ключ GigaChat из переменной установлен на сервере (LLM_PROVIDER=gigachat)"
fi

[ "${PUSH_ONLY:-0}" = 1 ] && { echo "код и веб залиты, deploy пропущен (PUSH_ONLY=1)"; exit 0; }
$SSH "somelye@$HOST" 'bash /opt/somelye/app/infra/cpulab/deploy.sh'
