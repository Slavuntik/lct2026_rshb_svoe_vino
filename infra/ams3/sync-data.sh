#!/usr/bin/env bash
# Данные стенда на ams3: CV-индекс, RAG-индекс, метаданные кейса, модели (~2.8 ГБ).
# Запускать с Mac разово и после каждой пересборки индекса. Пересобирать индекс НА сервере
# не нужно: это ~45 минут CPU на боевом VPN-боксе. Прерванный rsync просто запустить снова.
#
# Использование:
#   sync-data.sh <host> [ключ]                    — обычная заливка, 7 шагов (см. RAG_INDEX_DIR/DST ниже)
#   sync-data.sh switch-rag <host> <dst> [ключ]    — переключить RAG_DATA_DIR на /opt/somelye/data/<dst>:
#                                                     бэкап somelye.env, рестарт, ждать healthz warm +
#                                                     сверить rag_index_version. Идемпотентно (тот же <dst>
#                                                     повторно — версия не меняется, рестарт всё равно будет).
#   sync-data.sh rollback-rag <host> [ключ]        — откат RAG_DATA_DIR на каталог, который был ДО
#                                                     последнего switch-rag/rollback-rag (тумблер туда-обратно,
#                                                     как cd -). Без предыдущего switch-rag — ошибка.
#   DRY_RUN=1 перед любой из трёх форм — ничего не меняет (rsync -n / без sed и рестарта), только печатает,
#   что было бы сделано. Старые каталоги RAG_DATA_DIR НЕ удаляются автоматически никогда — только du -sh.
set -euo pipefail

ACTION="${1:-}"
case "$ACTION" in
  switch-rag)
    HOST="${2:?укажи хост ams3}"
    RAG_INDEX_DST="${3:?укажи каталог на сервере, напр. rag-20260922}"
    KEY="${4:-$HOME/.ssh/ci_do_ams3}"
    ;;
  rollback-rag)
    HOST="${2:?укажи хост ams3}"
    KEY="${3:-$HOME/.ssh/ci_do_ams3}"
    ;;
  *)
    HOST="${ACTION:?укажи хост ams3}"
    KEY="${2:-$HOME/.ssh/ci_do_ams3}"
    ;;
esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CASE="${CASE_DATA_DIR:-$ROOT/../case-data}"
SSH="ssh -i $KEY -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/somelye-%r@%h -o ControlPersist=300"
DST="somelye@$HOST:/opt/somelye/data"
put() { rsync -az ${DRY_RUN:+-n} -e "$SSH" "$@"; }
[ -n "${DRY_RUN:-}" ] && echo "DRY-RUN: ничего не меняю ни на диске, ни в somelye.env, ни в сервисе"

# rag_switch_to <абсолютный путь на сервере> <метка бэкапа> — общая процедура для switch-rag и
# rollback-rag: preflight (labels.jsonl/manifest.json на месте) -> печать текущего/целевого ->
# du -sh всех rag*-каталогов (старые НЕ удаляются, только показываются) -> под DRY_RUN стоп -> иначе
# бэкап somelye.env, сохранение ТЕКУЩЕГО значения в маркер отката (тумблер), sed, рестарт, ожидание
# healthz warm ТЕМ ЖЕ циклом, что deploy.sh (127.0.0.1:8000 — с самого сервера, не наружу), сверка
# rag_index_version с manifest.json целевого каталога, ExecMainStartTimestampMonotonic (operations.md
# §2.2 — растёт строго при каждом рестарте, надёжнее wall-clock).
rag_switch_to() {
  local newdir="$1" label="$2"
  $SSH "somelye@$HOST" bash -s -- "$newdir" "$label" "${DRY_RUN:-}" <<'EOF'
set -euo pipefail
NEWDIR="$1"; LABEL="$2"; DRYRUN="${3:-}"
F=/opt/somelye/somelye.env
PREVFILE=/opt/somelye/data/.rag_data_dir.prev

if [ ! -f "$NEWDIR/labels.jsonl" ] || [ ! -f "$NEWDIR/manifest.json" ]; then
  echo "целевой каталог $NEWDIR не похож на RAG-индекс (нет labels.jsonl и/или manifest.json)" >&2
  exit 1
fi
EXPECT_VER=$(grep -m1 '"version"' "$NEWDIR/manifest.json" | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')
CURRENT=$(grep -m1 '^RAG_DATA_DIR=' "$F" | cut -d= -f2-)

echo "RAG_DATA_DIR сейчас: $CURRENT"
echo "переключаю на:       $NEWDIR (manifest version $EXPECT_VER)"
[ "$CURRENT" = "$NEWDIR" ] && echo "(идемпотентно — то же значение; версия не изменится, но рестарт всё равно выполнится)"
echo "каталоги RAG на диске (старые НЕ удаляю автоматически):"
du -sh /opt/somelye/data/rag* 2>/dev/null || true

if [ -n "$DRYRUN" ]; then
  echo "DRY-RUN: env и рестарт не трогаю. Записал бы RAG_DATA_DIR=$NEWDIR в $F (бэкап $F.bak-before-$LABEL, предыдущее значение $CURRENT ушло бы в $PREVFILE)."
  exit 0
fi

cp "$F" "$F.bak-before-$LABEL"
echo "$CURRENT" > "$PREVFILE"
sed -i '/^RAG_DATA_DIR=/d' "$F"
printf 'RAG_DATA_DIR=%s\n' "$NEWDIR" >> "$F"

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

HZ=$(curl -fsS http://127.0.0.1:8000/v1/healthz)
echo "healthz: $HZ"
GOT_VER=$(printf '%s' "$HZ" | grep -o '"rag_index_version":"[^"]*"' | cut -d'"' -f4)
if [ "$GOT_VER" = "$EXPECT_VER" ]; then
  echo "rag_index_version подтверждён: $GOT_VER"
else
  echo "ВНИМАНИЕ: rag_index_version=$GOT_VER, ожидался $EXPECT_VER (манифест $NEWDIR)" >&2
fi
systemctl show -p ExecMainStartTimestamp,ExecMainStartTimestampMonotonic somelye-api
EOF
}

case "$ACTION" in
  switch-rag)
    rag_switch_to "/opt/somelye/data/$RAG_INDEX_DST" "rag-$(date +%Y%m%d-%H%M%S)"
    exit 0
    ;;
  rollback-rag)
    PREV=$($SSH "somelye@$HOST" bash -s -- <<'EOF' || true
cat /opt/somelye/data/.rag_data_dir.prev 2>/dev/null
EOF
)
    [ -n "$PREV" ] || { echo "нет сохранённого предыдущего RAG_DATA_DIR на сервере (.rag_data_dir.prev) — сначала switch-rag" >&2; exit 1; }
    rag_switch_to "$PREV" "rag-rollback-$(date +%Y%m%d-%H%M%S)"
    exit 0
    ;;
esac

# Какой CV-индекс везти (каталог с qdrant/ внутри). По умолчанию — боевой packages/cv/data;
# новый индекс (другой энкодер/эталоны) — CV_INDEX_DIR=packages/cv/data-d1 sync-data.sh <host>.
CV_INDEX_DIR="${CV_INDEX_DIR:-$ROOT/packages/cv/data}"
# Куда на сервере (подкаталог /opt/somelye/data). Новый индекс везём в ОТДЕЛЬНЫЙ каталог и
# переключаем CV_DATA_DIR в somelye.env — работающий стенд читает старый до рестарта:
#   CV_INDEX_DIR=packages/cv/data-d1 CV_INDEX_DST=cv-d1 sync-data.sh <host>
CV_INDEX_DST="${CV_INDEX_DST:-cv}"
# То же самое для RAG-индекса (по образцу CV_INDEX_DIR/CV_INDEX_DST выше), плюс — в отличие от CV —
# готовые switch-rag/rollback-rag делают остальное сами (см. "Использование" в шапке файла):
#   RAG_INDEX_DIR=case-data/rag-index-20261001 RAG_INDEX_DST=rag-20261001 sync-data.sh <host>   # 1: залить
#   sync-data.sh switch-rag <host> rag-20261001                                                  # 2: переключить
RAG_INDEX_DIR="${RAG_INDEX_DIR:-$ROOT/packages/rag/data}"
RAG_INDEX_DST="${RAG_INDEX_DST:-rag}"

echo "1/7 CV-индекс";          $SSH "somelye@$HOST" "mkdir -p /opt/somelye/data/$CV_INDEX_DST/qdrant"
                               put --delete --exclude '.lock' "$CV_INDEX_DIR/qdrant/" "$DST/$CV_INDEX_DST/qdrant/"
echo "2/7 RAG-индекс";         $SSH "somelye@$HOST" "mkdir -p /opt/somelye/data/$RAG_INDEX_DST"
                               put --delete --exclude '.lock' "$RAG_INDEX_DIR/" "$DST/$RAG_INDEX_DST/"
if [ -n "${DRY_RUN:-}" ]; then
  echo "    DRY-RUN: проверку целостности RAG пропускаю — данные не передавались"
else
  LOCAL_LABELS=$(wc -l < "$RAG_INDEX_DIR/labels.jsonl" | tr -d ' ')
  LOCAL_VERSION=$(grep -m1 '"version"' "$RAG_INDEX_DIR/manifest.json" | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')
  REMOTE_CHECK=$($SSH "somelye@$HOST" bash -s -- "/opt/somelye/data/$RAG_INDEX_DST" <<'EOF'
wc -l < "$1/labels.jsonl" | tr -d ' '
grep -m1 '"version"' "$1/manifest.json" | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/'
EOF
)
  REMOTE_LABELS=$(echo "$REMOTE_CHECK" | sed -n '1p')
  REMOTE_VERSION=$(echo "$REMOTE_CHECK" | sed -n '2p')
  echo "    целостность: labels.jsonl лок/удал $LOCAL_LABELS/$REMOTE_LABELS строк, manifest version лок/удал $LOCAL_VERSION/$REMOTE_VERSION"
  if [ "$LOCAL_LABELS" != "$REMOTE_LABELS" ] || [ "$LOCAL_VERSION" != "$REMOTE_VERSION" ]; then
    echo "ЦЕЛОСТНОСТЬ RAG-ИНДЕКСА В /opt/somelye/data/$RAG_INDEX_DST НЕ СОШЛАСЬ С ИСТОЧНИКОМ — не запускай switch-rag на этот каталог, разберись сначала" >&2
    exit 1
  fi
fi
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
