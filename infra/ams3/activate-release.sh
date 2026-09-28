#!/usr/bin/env bash
# Runs as somelye; only the existing API restart sudo permission is required.
set -euo pipefail
BASE=/opt/somelye
RELEASE="${1:?release directory required}"
case "$RELEASE" in "$BASE"/releases/*) ;; *) echo 'Invalid release path'; exit 1;; esac
exec 9>"$BASE/.deploy.lock"
flock -w 900 9
test -f "$RELEASE/app/apps/api/uv.lock"
test -f "$RELEASE/web/index.html"
test -f "$RELEASE/commit"
# A manual deployment and the queued CI job may target the same SHA.
# Avoid a second restart only after checking the running API and published UI.
if cmp -s "$RELEASE/commit" "$BASE/deployed-commit"; then
  if "$BASE/venv/bin/python" "$BASE/app/infra/ams3/check-release.py" && \
     "$BASE/venv/bin/python" -c 'import json,sys,urllib.request; from pathlib import Path; assert json.load(urllib.request.urlopen("http://127.0.0.1/release.json", timeout=10))["commit"] == Path(sys.argv[1]).read_text().strip()' "$RELEASE/commit"; then
    echo 'Эта ревизия уже активна и прошла проверку; повторный рестарт не нужен'
    exit 0
  fi
fi
if [ "${SKIP_API_RESTART:-0}" = 1 ]; then
  "$BASE/venv/bin/python" "$RELEASE/app/infra/ams3/runtime-unchanged.py" "$BASE/app" "$RELEASE/app"
  "$BASE/venv/bin/python" "$RELEASE/app/infra/ams3/check-release.py"
fi
BACKUP="$RELEASE/previous"
mkdir "$BACKUP"
cp -p "$BASE/somelye.env" "$BACKUP/somelye.env"
app_moved=0
web_moved=0
rollback() {
  rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ]; then
    echo 'Новый релиз не прошёл проверку, восстанавливаем предыдущий'
    if [ "$app_moved" = 1 ]; then
      [ ! -e "$BASE/app" ] || mv "$BASE/app" "$RELEASE/failed-app"
      mv "$BACKUP/app" "$BASE/app"
    fi
    if [ "$web_moved" = 1 ]; then
      [ ! -e "$BASE/web" ] || mv "$BASE/web" "$RELEASE/failed-web"
      mv "$BACKUP/web" "$BASE/web"
    fi
    if [ "$app_moved" = 1 ]; then
      bash "$BASE/app/infra/ams3/deploy.sh" || echo 'ОШИБКА: автоматический откат API требует проверки'
    fi
  fi
  exit "$rc"
}
trap rollback EXIT
if [ "${SKIP_API_RESTART:-0}" != 1 ]; then
  mv "$BASE/app" "$BACKUP/app"
  app_moved=1
  mv "$RELEASE/app" "$BASE/app"
  bash "$BASE/app/infra/ams3/deploy.sh"
fi
# Only publish the UI once the API is warm and the configured shelf is reachable.
# UI-only releases leave the live app tree (including runtime files) untouched.
GATE="$BASE/app/infra/ams3/check-release.py"
[ "${SKIP_API_RESTART:-0}" != 1 ] || GATE="$RELEASE/app/infra/ams3/check-release.py"
"$BASE/venv/bin/python" "$GATE"
mv "$BASE/web" "$BACKUP/web"
web_moved=1
mv "$RELEASE/web" "$BASE/web"
# nginx's worker must be able to traverse/read assets created under deployment umask.
chmod -R a+rX "$BASE/web"
curl --fail --silent --show-error http://127.0.0.1/release.json
cp "$RELEASE/commit" "$BASE/deployed-commit"
printf '\nРелиз активен: %s; предыдущий сохранён: %s\n' "$(cat "$RELEASE/commit")" "$BACKUP"
