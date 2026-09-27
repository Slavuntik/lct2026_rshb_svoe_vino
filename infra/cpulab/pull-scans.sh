#!/usr/bin/env bash
# Забирает архив сканов cpulab к себе (вне git — в case-data). Копия infra/ams3/pull-scans.sh
# с другим дефолтным хостом/ключом. Куда: $CASE_DATA_DIR/stand-scans (по умолчанию ../case-data) —
# ОБЩИЙ каталог с ams3: если нужно различать источник, синхронизируй в разное время или
# передай CASE_DATA_DIR с отдельным путём для этого стенда.
#
# Использование: pull-scans.sh [host] [ключ]
set -euo pipefail
HOST="${1:-46.243.211.32}"
KEY="${2:-$HOME/.ssh/cpu_lab}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${CASE_DATA_DIR:-$ROOT/../case-data}/stand-scans"
mkdir -p "$DEST"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" "somelye@$HOST:/opt/somelye/data/scans/" "$DEST/"
echo "архив: $DEST"
for b in confident unsure failed; do
  printf "  %-10s %s фото\n" "$b" "$(find "$DEST/$b" -name '*.jpg' 2>/dev/null | wc -l | tr -d ' ')"
done
