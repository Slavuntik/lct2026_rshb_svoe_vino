#!/usr/bin/env bash
# Выполняется НА сервере от пользователя somelye — после того, как push-release.sh залил код
# в /opt/somelye/app и сборку веба в /opt/somelye/web. Ставит зависимости, перезапускает API
# и не отпускает, пока стенд не прогреется (healthz warm:true) — иначе падает с логом.
#
# Отличие от infra/ams3/deploy.sh: uv живёт в РЕАЛЬНОМ $HOME пользователя (/home/somelye —
# обычный логин-юзер этого сервера), а не в /opt/somelye/.local/bin как на ams3 (там
# системный пользователь с --home-dir /opt/somelye). "$HOME/.local/bin/uv" работает на
# обоих стендах без правки — если когда-нибудь понадобится один скрипт на оба хоста, ams3
# можно смело перевести на ту же запись.
set -euo pipefail
BASE=/opt/somelye
UV="$HOME/.local/bin/uv"

cd "$BASE/app/apps/api"
UV_PROJECT_ENVIRONMENT="$BASE/venv" "$UV" sync --frozen --no-dev --extra integration --python 3.12 --quiet

# Предзагрузка моделей RapidOCR (ONNX Runtime) — идемпотентный вызов ДО рестарта, тот же
# приём, что infra/ams3/deploy.sh (см. его комментарий про H2-rapidocr-multiscale.md):
# сеть на этом сервере есть, качаем сейчас, а не на первый боевой запрос.
"$BASE/venv/bin/python" -c "
from cv.ocr_rapid import RapidOcrReader, DEFAULT_RAPID_SIZES
import numpy as np
RapidOcrReader(sizes=DEFAULT_RAPID_SIZES).read(np.zeros((32, 32, 3), dtype=np.uint8))
print('RapidOCR: модели', DEFAULT_RAPID_SIZES, 'на диске')
" || echo "предзагрузка моделей RapidOCR не удалась (сеть?) — прогреется на первый боевой запрос при CV_OCR_ENGINE=rapid"

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
