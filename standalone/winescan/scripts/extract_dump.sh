#!/usr/bin/env bash
# Распаковывает многотомный RAR-дамп Strapi (prod-svoe-vino-strapi.part{1,2,3}.rar) в data/raw/.
#
# Нужен unrar с поддержкой RAR5: sudo apt-get install unrar (репозиторий multiverse).
# 7z из Ubuntu 24.04 (7zip 23.01) без плагина 7zip-rar не умеет сжатые записи RAR5:
# пишет "Unsupported Method" и оставляет пустые файлы.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
data_dir="${WINESCAN_DATA_DIR:-$root/data}"
archive="$data_dir/prod-svoe-vino-strapi.part1.rar"
out_dir="$data_dir/raw"
uploads="$out_dir/prod-svoe-vino-strapi/prod-svoe-vino/strapi/uploads"

command -v unrar >/dev/null 2>&1 || { echo "ERROR: unrar not found (sudo apt-get install unrar)" >&2; exit 1; }
[ -f "$archive" ] || { echo "ERROR: archive not found: $archive" >&2; exit 1; }

mkdir -p "$out_dir"
# -o+ перезаписывает, -idq печатает только ошибки; unrar сам сверяет CRC и вернёт != 0 при порче
unrar x -o+ -idq "$archive" "$out_dir/"

total=$(find "$uploads" -type f | wc -l)
empty=$(find "$uploads" -type f -empty | wc -l)
echo "extracted: $total files, empty: $empty -> $uploads"
[ "$empty" -eq 0 ] || { echo "ERROR: empty files after extraction" >&2; exit 1; }
