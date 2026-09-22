#!/bin/zsh
# Сервер для приватной проверки на созвоне: их participant_test.sh по умолчанию шлёт фото на
# http://127.0.0.1:8080/v1/eval/predict. Здесь — лучшая конфигурация на Mac (Apple Silicon):
#   CV base-384 (индекс после чистки эталонов, packages/cv/data-d1) + текст этикетки от ДВУХ
#   моделей параллельно: локальная Qwen3-VL-4B (служба ~/ClaudeWorkspace/local-vlm, :8093) и
#   27B на нашем GPU-сервере (шлюз). Не успела модель к дедлайну — работает вторая или RapidOCR
#   (agents/H2-rapidocr-multiscale.md — фолбэк был PaddleOCR, теперь быстрее и не хуже по
#   точности на этом же кадре).
# Приёмка 21.09 (62 живых фото из каталога): обе модели — top-1 95.2%, медиана 5.3 с.
# ML-1 (reports/ml-eng-ml1.md, 22.09): CV_FUSION_MERGE_MODEL_TEXT=1 ниже — OCR добавляется к
# тексту модели через пробел, не только фолбэк на её молчание — режим vlm 95.2% → 96.8% top-1.
#
# Адрес и ключ шлюза — из файла вне репозитория (по умолчанию vines/vlm-lab/.env, chmod 600,
# строки LLM_GATEWAY_URL=... и LLM_GATEWAY_KEY=...). Нет файла — только локальная модель.
#
# CV_OCR_ENGINE=rapid + CV_FUSION_CROPS=8 (agents/H2-rapidocr-multiscale.md): бюджет на Mac
# заметно свободнее, чем на ams3 (4 vCPU) — 8 кропов CV + двухмасштабный RapidOCR как фолбэк
# укладываются с запасом, точность выше (офлайн 93.5% против 90.3% у 2 кропов).
#
# CV_OCR_LABEL_SIZE=1280 (agents/H3-label-crop-ocr.md): третий проход — детектор+
# распознаватель RapidOCR на КРОПЕ ЭТИКЕТКИ (боксы из прохода 960 выше переиспользуются,
# лишней детекции нет) — +1 фото офлайн (90.3% -> 91.9% top-1, 2 кропа CV). "0" выключает.
#
# ML-2 (reports/ml-eng-ml2.md, 22.09): SHELF_CROP=1 ниже — сегментация кадра ЦЕЛОЙ ПОЛКИ
# на бутылки перед конвейером (packages/cv/cv/shelf_crop.py). Дефолт "0" (выключено) —
# живая приёмка нашла РЕГРЕССИЮ на 62 фото каталога (59/62 -> 58/62, near-dup спутан
# ложным срабатыванием гейта на одиночной бутылке) и НЕ показала обещанный офлайн прирост
# на 8 полевых целевых (1/8, как и без флага) — не готово к бою, инфраструктура оставлена
# для дальнейшей работы поверх неё.
#
# Использование: infra/local-check/run-check-server.sh        (порт 8080)
#                PORT=8081 TEXT_SOURCE=vlm_local infra/local-check/run-check-server.sh
#                SHELF_CROP=1 infra/local-check/run-check-server.sh   (см. предупреждение выше)
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${CASE_DATA_DIR:-$R/../case-data}"
GW_ENV="${VLM_GATEWAY_ENV:-$R/../vlm-lab/.env}"
if [ -f "$GW_ENV" ]; then set -a; . "$GW_ENV"; set +a; fi
curl -s -m 3 http://127.0.0.1:8093/v1/models >/dev/null || echo "ВНИМАНИЕ: локальная модель не отвечает на :8093 — ~/ClaudeWorkspace/local-vlm/status.sh"
[ -d "$R/packages/cv/data-d1/qdrant" ] || { echo "нет индекса packages/cv/data-d1 — см. reports/d1-ref-collisions.md"; exit 1; }
cd "$R/apps/api"
exec env \
  DATABASE_URL="sqlite:///${TMPDIR:-/tmp}/somelye-check.db" \
  JWT_SECRET="${JWT_SECRET:-local-check-$(date +%s)-secret-32-bytes-minimum}" \
  IMAGE_PROVIDER=real VERIFIER_PROVIDER=real RAG_PROVIDER=real RAG_MODE=embedded \
  RAG_DATA_DIR="$R/packages/rag/data" RAG_FASTEMBED_CACHE="$R/packages/rag/.fastembed_cache" \
  CV_DATA_DIR="$R/packages/cv/data-d1" CV_MODEL=google/siglip2-base-patch16-384 \
  CASE_DATA_DIR="$CASE" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  LLM_PROVIDER=mock \
  CV_FUSION=1 CV_FUSION_W=0.3 CV_FUSION_MERGE_MODEL_TEXT=1 CV_FUSION_TEXT_SOURCE="${TEXT_SOURCE:-vlm_both}" \
  CV_OCR_ENGINE="${OCR_ENGINE:-rapid}" CV_OCR_RAPID_SIZES="${OCR_RAPID_SIZES:-640,960}" \
  CV_OCR_LABEL_SIZE="${OCR_LABEL_SIZE:-1280}" \
  CV_FUSION_CROPS="${FUSION_CROPS:-8}" \
  CV_SHELF_CROP="${SHELF_CROP:-0}" CV_SHELF_MIN_BOXES="${SHELF_MIN_BOXES:-30}" \
  CV_SHELF_CHECK_NEIGHBORS="${SHELF_CHECK_NEIGHBORS:-0}" \
  VISION_LLM_URL="${LLM_GATEWAY_URL:-}" VISION_LLM_KEY="${LLM_GATEWAY_KEY:-}" \
  VISION_LLM_LOCAL_URL=http://127.0.0.1:8093/v1 VISION_LLM_TIMEOUT_S="${VISION_LLM_TIMEOUT_S:-6.5}" \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8080}"
