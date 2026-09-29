#!/usr/bin/env bash
# Publish the allowlisted CLI bundle only for the currently active Git release.
set -euo pipefail
BASE=/opt/somelye
RELEASE="${1:?release directory required}"
case "$RELEASE" in "$BASE"/releases/*) ;; *) echo 'Invalid release path'; exit 1;; esac
exec 9>"$BASE/.deploy.lock"
flock -w 900 9
if ! cmp -s "$RELEASE/commit" "$BASE/deployed-commit"; then
  echo 'Пакет CLI устаревшего релиза не публикуется'
  exit 0
fi
test -f "$RELEASE/tooling/tools/eval_detector.py"
test -f "$RELEASE/tooling/eval/participant_test.sh"
test -f "$RELEASE/tooling/docs/EVAL_DEMO.md"
if [ -e "$BASE/tooling" ] && [ ! -L "$BASE/tooling" ]; then
  echo 'Путь tooling занят обычным файлом/директорией; оставляем его без изменений'
  exit 1
fi
cp "$RELEASE/commit" "$RELEASE/tooling/.git-revision"
ln -s "$RELEASE/tooling" "$RELEASE/tooling-link-$$"
mv -Tf "$RELEASE/tooling-link-$$" "$BASE/tooling"
printf 'CLI и инструкция активны: %s\n' "$(cat "$RELEASE/commit")"
