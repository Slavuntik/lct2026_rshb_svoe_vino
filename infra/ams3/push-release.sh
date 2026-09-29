#!/usr/bin/env bash
# Build one immutable Git revision before uploading anything. Activate with rollback.
set -euo pipefail
HOST="${1:?укажи хост ams3}"
KEY="${2:-$HOME/.ssh/ci_do_ams3}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
REF="$(git -C "$ROOT" rev-parse "${DEPLOY_REF:-HEAD}^{commit}")"
SSH=(ssh -i "$KEY" -o UserKnownHostsFile="${SSH_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}" -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-%r@%h -o ControlPersist=120)

# Older queued CI runs must not roll back a newer main. Explicit tags/manual rollback remain supported.
if [ "${GITHUB_EVENT_NAME:-}" = push ] && [ "${GITHUB_REF:-}" = refs/heads/main ]; then
  latest="$(git -C "$ROOT" ls-remote origin refs/heads/main | cut -f1)"
  [ "$latest" = "$REF" ] || { echo "main уже обновлён: устаревший запуск $REF пропущен"; exit 0; }
fi

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
git -C "$ROOT" archive "$REF" apps/web apps/shell/plugins/ocr-plugin | tar -x -C "$BUILD"
(cd "$BUILD/apps/web" && npm ci --silent --no-progress && VITE_API_MODE=real VITE_THEME=portal npm run build --silent)
test -f "$BUILD/apps/web/dist/index.html"
printf '{"commit":"%s"}\n' "$REF" > "$BUILD/apps/web/dist/release.json"

RELEASE="$REF-$(date +%s)-$$"
REMOTE="/opt/somelye/releases/$RELEASE"
"${SSH[@]}" "somelye@$HOST" "umask 077; mkdir -p '$REMOTE/app' '$REMOTE/web' '$REMOTE/tooling'"
git -C "$ROOT" archive "$REF" apps/api apps/shelf-finder/server packages contracts infra pipeline/ref pipeline/catalog pipeline/build | gzip \
  | "${SSH[@]}" "somelye@$HOST" "tar xzf - -C '$REMOTE/app'"
tar -czf - -C "$BUILD/apps/web/dist" . | "${SSH[@]}" "somelye@$HOST" "tar xzf - -C '$REMOTE/web'"
# Explicit CLI/documentation allowlist: no photos, model data, local reports or secrets.
git -C "$ROOT" archive "$REF" tools/eval_detector.py tools/test_eval_detector.py eval/participant_test.sh README.md TESTING.md docs/EVAL_DEMO.md | gzip \
  | "${SSH[@]}" "somelye@$HOST" "tar xzf - -C '$REMOTE/tooling'"
printf '%s\n' "$REF" | "${SSH[@]}" "somelye@$HOST" "cat > '$REMOTE/commit'"
# Existing production environment (including LLM keys) is deliberately preserved.
[ "${PUSH_ONLY:-0}" = 1 ] && { echo "Релиз подготовлен в $REMOTE; работающий сервер не изменён"; exit 0; }
"${SSH[@]}" "somelye@$HOST" "SKIP_API_RESTART=${SKIP_API_RESTART:-0} bash '$REMOTE/app/infra/ams3/activate-release.sh' '$REMOTE'"

"${SSH[@]}" "somelye@$HOST" "bash '$REMOTE/app/infra/ams3/publish-tooling.sh' '$REMOTE'"
