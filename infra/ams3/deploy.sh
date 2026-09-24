#!/usr/bin/env bash
# Выполняется НА сервере от пользователя somelye — после того, как push-release.sh залил код
# в /opt/somelye/app и сборку веба в /opt/somelye/web. Ставит зависимости, перезапускает API
# и не отпускает, пока стенд не прогреется (healthz warm:true) — иначе падает с логом.
set -euo pipefail
BASE=/opt/somelye
UV="$BASE/.local/bin/uv"

cd "$BASE/app/apps/api"
UV_PROJECT_ENVIRONMENT="$BASE/venv" "$UV" sync --frozen --no-dev --extra integration --python 3.12 --quiet

# agents/H2-rapidocr-multiscale.md: предзагрузка моделей RapidOCR (ONNX Runtime) —
# идемпотентный вызов создания движка ДО рестарта, под пользователем сервиса somelye
# (тот же пользователь, что владеет venv/кэшами моделей ниже). Сеть на ams3 есть (в
# отличие от боевого запроса из-под VPN-бокса, где сетевой поход посреди запроса
# недопустим) — качаем СЕЙЧАС, а не на первый боевой запрос при CV_OCR_ENGINE=rapid.
# Безусловно (не проверяем текущий CV_OCR_ENGINE в somelye.env): переключение движка
# рестартом без нового деплоя не должно ловить холодную сеть при первом запросе.
# Модели кэшируются РапидOCR внутри своего пакета (site-packages/rapidocr/models/) —
# venv переживает рестарт сервиса, повторный вызов при уже скачанных файлах — просто
# быстрая проверка на диске, не скачивание заново. Сбой (сеть недоступна именно
# сейчас) не должен ронять деплой paddle-пути (дефолт CV_OCR_ENGINE) — предзагрузка
# тогда просто откладывается на первый боевой rapid-запрос (см. cv/ocr_rapid.py).
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
