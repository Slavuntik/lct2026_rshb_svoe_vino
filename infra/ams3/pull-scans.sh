#!/usr/bin/env bash
# Забирает архив сканов стенда к себе (вне git — в case-data) и печатает сводку по корзинам.
# Фото людей и мест в git не попадают никогда: каталог лежит рядом с репозиторием, не в нём.
#
# Использование: pull-scans.sh <host> [ключ]     Куда: $CASE_DATA_DIR/stand-scans (по умолчанию ../case-data)
set -euo pipefail
HOST="${1:?укажи хост ams3}"
KEY="${2:-$HOME/.ssh/ci_do_ams3}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="${CASE_DATA_DIR:-$ROOT/../case-data}/stand-scans"
mkdir -p "$DEST"
rsync -az -e "ssh -i $KEY -o BatchMode=yes" "somelye@$HOST:/opt/somelye/data/scans/" "$DEST/"
echo "архив: $DEST"
for b in confident unsure failed; do
  printf "  %-10s %s фото\n" "$b" "$(find "$DEST/$b" -name '*.jpg' 2>/dev/null | wc -l | tr -d ' ')"
done
