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
BACKUP="$RELEASE/previous"
mkdir "$BACKUP"
cp -p "$BASE/somelye.env" "$BACKUP/somelye.env"
app_moved=0
web_moved=0
rollback() {
  rc=$?
  trap - EXIT
  if [ "$rc" -ne 0 ] && [ "$app_moved" = 1 ]; then
    echo 'Новый релиз не прошёл проверку, восстанавливаем предыдущий'
    [ ! -e "$BASE/app" ] || mv "$BASE/app" "$RELEASE/failed-app"
    mv "$BACKUP/app" "$BASE/app"
    if [ "$web_moved" = 1 ]; then
      [ ! -e "$BASE/web" ] || mv "$BASE/web" "$RELEASE/failed-web"
      mv "$BACKUP/web" "$BASE/web"
    fi
    bash "$BASE/app/infra/ams3/deploy.sh" || echo 'ОШИБКА: автоматический откат API требует проверки'
  fi
  exit "$rc"
}
trap rollback EXIT
mv "$BASE/app" "$BACKUP/app"
app_moved=1
mv "$RELEASE/app" "$BASE/app"
bash "$BASE/app/infra/ams3/deploy.sh"
# Only publish the UI once the API is warm and the configured shelf is reachable.
"$BASE/venv/bin/python" "$BASE/app/infra/ams3/check-release.py"
mv "$BASE/web" "$BACKUP/web"
web_moved=1
mv "$RELEASE/web" "$BASE/web"
# nginx's worker must be able to traverse/read assets created under deployment umask.
chmod -R a+rX "$BASE/web"
cp "$RELEASE/commit" "$BASE/deployed-commit"
curl --fail --silent --show-error http://127.0.0.1/release.json
printf '\nРелиз активен: %s; предыдущий сохранён: %s\n' "$(cat "$RELEASE/commit")" "$BACKUP"
