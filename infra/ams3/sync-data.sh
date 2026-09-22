#!/usr/bin/env bash
# Данные стенда на ams3: CV-индекс, RAG-индекс, метаданные кейса, модели (~2.8 ГБ).
# Запускать с Mac разово и после каждой пересборки индекса. Пересобирать индекс НА сервере
# не нужно: это ~45 минут CPU на боевом VPN-боксе. Прерванный rsync просто запустить снова.
#
# Использование: sync-data.sh <host> [путь к SSH-ключу CI]
set -euo pipefail
HOST="${1:?укажи хост ams3}"
KEY="${2:-$HOME/.ssh/ci_do_ams3}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${CASE_DATA_DIR:-$ROOT/../case-data}"
# Какой CV-индекс везти (каталог с qdrant/ внутри). По умолчанию — боевой packages/cv/data;
# новый индекс (другой энкодер/эталоны) — CV_INDEX_DIR=packages/cv/data-d1 sync-data.sh <host>.
CV_INDEX_DIR="${CV_INDEX_DIR:-$ROOT/packages/cv/data}"
# Куда на сервере (подкаталог /opt/somelye/data). Новый индекс везём в ОТДЕЛЬНЫЙ каталог и
# переключаем CV_DATA_DIR в somelye.env — работающий стенд читает старый до рестарта:
#   CV_INDEX_DIR=packages/cv/data-d1 CV_INDEX_DST=cv-d1 sync-data.sh <host>
CV_INDEX_DST="${CV_INDEX_DST:-cv}"
SSH="ssh -i $KEY -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-%r@%h -o ControlPersist=300"
DST="somelye@$HOST:/opt/somelye/data"
put() { rsync -az -e "$SSH" "$@"; }

echo "1/7 CV-индекс";          $SSH "somelye@$HOST" "mkdir -p /opt/somelye/data/$CV_INDEX_DST/qdrant"
                               put --delete --exclude '.lock' "$CV_INDEX_DIR/qdrant/" "$DST/$CV_INDEX_DST/qdrant/"
echo "2/7 RAG-индекс";         put "$ROOT/packages/rag/data/" "$DST/rag/"
echo "3/7 модели RAG";         put "$ROOT/packages/rag/.fastembed_cache/" "$DST/fastembed/"
echo "4/7 SigLIP2";            $SSH "somelye@$HOST" 'mkdir -p /opt/somelye/data/models/hf/hub'
                               put "$HOME/.cache/huggingface/hub/models--google--siglip2-base-patch16-224" "$DST/models/hf/hub/"
                               # base-384 — энкодер индекса с 21.09 (reports/g6-encoder.md: +6 п.п. top-1 на живых фото)
                               put "$HOME/.cache/huggingface/hub/models--google--siglip2-base-patch16-384" "$DST/models/hf/hub/"
echo "5/7 PaddleOCR";          put "$HOME/.paddlex/official_models" "$DST/models/paddlex/"
echo "6/7 кейс и метрики";     put "$CASE/slug_refs.json" "$CASE/families.json" "$DST/case/"
                               # сырой CSV каталога — текстовый индекс слияния CV+текст (cv/text_fusion.py)
                               put "$CASE/strapi_output0709.csv" "$DST/case/"
                               # алиасы виноделен (ML-1, reports/ml-eng-ml1.md): cv/text_fusion.py::
                               # default_winery_aliases_path() читает $CASE_DATA_DIR/winery_aliases.json;
                               # файл вне git — только этим sync (или разовым rsync ниже в README).
                               put "$CASE/winery_aliases.json" "$DST/case/"
                               put "$ROOT/qa/scan-eval-runs/real-photos-stand/eval_report_snapshot.json" "$DST/eval_report_snapshot.json"
# agents/B8-candidates-card.md (contracts/image-scan.md v0.4.11): фолбэк-карточка
# кейс-слагов вне нашего RAG-каталога — case_catalog.json (apps/api/scripts/
# build_case_catalog.py) и превью thumbs/ (apps/api/scripts/build_case_thumbs.py),
# оба читает apps/api из ТОГО ЖЕ $CASE_DATA_DIR/... что и slug_refs.json выше
# (server: /opt/somelye/data/case, somelye.env.example) — тот же put в "$DST/case/".
echo "7/7 карточка кейса";     put "$CASE/case_catalog.json" "$DST/case/"
                               put "$CASE/thumbs/" "$DST/case/thumbs/"

echo "индекс на сервере:"; $SSH "somelye@$HOST" 'cat /opt/somelye/data/cv/qdrant/manifest.json; du -sh /opt/somelye/data'
