#!/usr/bin/env bash
# Выполняется НА сервере от пользователя somelye — после того, как push-release.sh залил код
# в /opt/somelye/app и сборку веба в /opt/somelye/web. Ставит зависимости, перезапускает API
# и не отпускает, пока стенд не прогреется (healthz warm:true) — иначе падает с логом.
set -euo pipefail
BASE=/opt/somelye
UV="$BASE/.local/bin/uv"

cd "$BASE/app/apps/api"
UV_PROJECT_ENVIRONMENT="$BASE/venv" "$UV" sync --frozen --no-dev --extra integration --python 3.12 --quiet

sudo /usr/bin/systemctl restart somelye-api

deadline=$((SECONDS + 900))
until curl -fsS http://127.0.0.1:8000/v1/healthz 2>/dev/null | grep -q '"warm":true'; do
  if ! sudo /usr/bin/systemctl is-active somelye-api >/dev/null 2>&1; then
    echo "somelye-api упал при старте:"
    sudo /usr/bin/systemctl status somelye-api --no-pager | tail -25
    exit 1
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "стенд не прогрелся за 15 минут"
    exit 1
  fi
  sleep 5
done

echo "healthz: $(curl -fsS http://127.0.0.1:8000/v1/healthz)"
echo "metrics: $(curl -fsS http://127.0.0.1:8000/v1/metrics/scan)"
