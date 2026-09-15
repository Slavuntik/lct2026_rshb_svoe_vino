#!/usr/bin/env bash
# qa/mock_case_script.sh — рехёрсал скрипта оценки кейсодержателя (агент F).
#
# case.md, п.6 ("Совместимость со скриптом оценки"): "bash шлёт фото по одному; на каждое —
# плоский JSON {"slug": "wine-slug"}; скрипт сам меряет время; сравнение с закрытой таблицей
# slug'ов". Их код нам не виден — только это описание. Этот скрипт делает ровно то же самое
# по буквальному прочтению ТЗ (curl -F, POST .../scan/photo?flat=1, разбор {"slug": ...},
# curl -w для тайминга), чтобы механика (multipart -> плоский JSON -> сравнение -> тайминг)
# была проверена ДО приезда их настоящего скрипта (contracts/image-scan.md: датасет и скрипт
# кейса — "завтра", локально, CASE_DATA_DIR). Когда скрипт приедет — сверить построчно и
# заменить этот файл либо пометить расхождения, не удалять рехёрсал молча.
#
# Разметка: labels.csv в каталоге фото (или $LABELS_CSV) — те же алиасы колонок, что у
# qa/scan_eval.py (переиспользует его load_eval_set/infer_slug_from_filename напрямую —
# одна логика разбора разметки на оба инструмента, не две расходящиеся копии).
#
# Использование:
#   python3 qa/mock_scan_server.py --port 8100 &                       # свой мок для рехёрсала
#   API_URL=http://localhost:8100 qa/mock_case_script.sh qa/tests/fixtures/scan_mini
#   API_URL=http://localhost:8000 qa/mock_case_script.sh case-data/public    # против настоящего apps/api
#
# Код возврата: 0 — каждое фото получило валидный плоский {"slug": непустая строка} ответ
# (совпадение с true_slug НЕ гарантировано и НЕ требуется для кода 0 — это рехёрсал
# механики, не приёмка качества; для качества — qa/scan_eval.py); 1 — хотя бы один ответ не
# был валидным плоским JSON со slug (их скрипт такое тоже не переживёт — сигнал серьёзнее,
# чем просто неверный slug); 2 — ошибка использования (каталог/labels не найдены).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

API_URL="${API_URL:-http://localhost:8100}"
PHOTOS_DIR="${1:-}"
LABELS_CSV="${LABELS_CSV:-}"

if [[ -z "$PHOTOS_DIR" ]]; then
    printf 'Использование: %s <каталог-фото>   (env: API_URL, LABELS_CSV)\n' "$0" >&2
    exit 2
fi
if [[ ! -d "$PHOTOS_DIR" ]]; then
    printf 'ОШИБКА: каталог не найден: %s\n' "$PHOTOS_DIR" >&2
    exit 2
fi
if [[ -z "$LABELS_CSV" && -f "$PHOTOS_DIR/labels.csv" ]]; then
    LABELS_CSV="$PHOTOS_DIR/labels.csv"
fi

# Список файлов — без mapfile/readarray: macOS до сих пор поставляет bash 3.2 (см.
# infra/scripts/validate.sh, тот же приём).
PHOTOS=()
while IFS= read -r f; do
    PHOTOS+=("$f")
done < <(find "$PHOTOS_DIR" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.bmp' \) \
    | sort)

if [[ "${#PHOTOS[@]}" -eq 0 ]]; then
    printf 'ОШИБКА: в %s нет изображений (.jpg/.jpeg/.png/.webp/.bmp)\n' "$PHOTOS_DIR" >&2
    exit 2
fi

LABELS_TSV="$(mktemp)"
RESULTS_CSV="$(mktemp)"
trap 'rm -f "$LABELS_TSV" "$RESULTS_CSV"' EXIT

# Разметка — через настоящий load_eval_set из scan_eval.py (одна логика на оба инструмента).
PYTHONPATH="$SCRIPT_DIR" python3 - "$PHOTOS_DIR" "$LABELS_CSV" "$LABELS_TSV" <<'PYEOF'
import sys
from pathlib import Path
from scan_eval import load_eval_set

photos_dir = Path(sys.argv[1])
labels_csv = Path(sys.argv[2]) if sys.argv[2] else None
out_path = Path(sys.argv[3])

items, warnings = load_eval_set(photos_dir, labels_csv)
with out_path.open("w", encoding="utf-8") as fh:
    for it in items:
        fh.write(f"{it.photo_id}\t{it.true_slug}\n")
for w in warnings:
    print(f"[mock_case_script] WARNING {w}", file=sys.stderr)
PYEOF

if [[ ! -s "$LABELS_TSV" ]]; then
    printf 'ОШИБКА: разметка пуста — нечего слать (см. предупреждения выше)\n' >&2
    exit 2
fi

printf 'photo,expected_slug,got_slug,http_status,time_total_s,match\n' > "$RESULTS_CSV"

printf '[mock_case_script] цель: %s, фото: %d\n' "$API_URL" "${#PHOTOS[@]}"

FAIL=0
i=0
for photo in "${PHOTOS[@]}"; do
    i=$((i + 1))
    name="$(basename "$photo")"
    expected="$(awk -F'\t' -v n="$name" '$1==n {print $2; exit}' "$LABELS_TSV")"
    if [[ -z "$expected" ]]; then
        printf '  [%d/%d] %s -> пропущено (нет в разметке)\n' "$i" "${#PHOTOS[@]}" "$name" >&2
        continue
    fi

    RESP_BODY="$(mktemp)"
    HTTP_LINE="$(curl -s -o "$RESP_BODY" -w '%{http_code} %{time_total}' \
        -X POST "${API_URL%/}/v1/scan/photo?flat=1" \
        -F "image=@${photo}")"
    CURL_EXIT=$?
    STATUS="${HTTP_LINE%% *}"
    TIME_TOTAL="${HTTP_LINE#* }"

    if [[ "$CURL_EXIT" -ne 0 ]]; then
        printf '  [%d/%d] %s -> curl код %d (API недоступен? %s)\n' \
            "$i" "${#PHOTOS[@]}" "$name" "$CURL_EXIT" "$API_URL" >&2
        FAIL=1
        printf '%s,%s,,curl_error_%d,,0\n' "$name" "$expected" "$CURL_EXIT" >> "$RESULTS_CSV"
        rm -f "$RESP_BODY"
        continue
    fi

    GOT_SLUG="$(python3 -c "
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    slug = data.get('slug', '')
    print(slug if isinstance(slug, str) else '')
except Exception:
    print('')
" "$RESP_BODY")"
    rm -f "$RESP_BODY"

    MATCH=0
    if [[ -z "$GOT_SLUG" || "$STATUS" != "200" ]]; then
        FAIL=1
        printf '  [%d/%d] %s -> HTTP %s, ПУСТОЙ/НЕВАЛИДНЫЙ ответ (ожидали %s) — их скрипт это тоже не переживёт\n' \
            "$i" "${#PHOTOS[@]}" "$name" "$STATUS" "$expected" >&2
    else
        [[ "$GOT_SLUG" == "$expected" ]] && MATCH=1
        TIME_MS="$(python3 -c "print(f'{float(\"$TIME_TOTAL\") * 1000:.0f}')")"
        if [[ "$MATCH" -eq 1 ]]; then
            printf '  [%d/%d] %s -> %s (%sмс) OK\n' "$i" "${#PHOTOS[@]}" "$name" "$GOT_SLUG" "$TIME_MS"
        else
            printf '  [%d/%d] %s -> %s (%sмс) MISS (ожидали %s)\n' \
                "$i" "${#PHOTOS[@]}" "$name" "$GOT_SLUG" "$TIME_MS" "$expected"
        fi
    fi

    printf '%s,%s,%s,%s,%s,%s\n' "$name" "$expected" "$GOT_SLUG" "$STATUS" "$TIME_TOTAL" "$MATCH" >> "$RESULTS_CSV"
done

PYTHONPATH="$SCRIPT_DIR" python3 - "$RESULTS_CSV" <<'PYEOF'
import csv
import sys
from scan_eval import _percentile

with open(sys.argv[1], newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))

valid = [r for r in rows if r["http_status"] == "200" and r["got_slug"]]
n = len(rows)
n_valid = len(valid)
matches = sum(1 for r in valid if r["match"] == "1")
latencies_ms = [float(r["time_total_s"]) * 1000 for r in valid]

print("\n[mock_case_script] --- сводка (рехёрсал их описанного поведения, не их код) ---")
print(f"[mock_case_script] отправлено: {n}, валидных плоских ответов: {n_valid}")
if n_valid:
    print(f"[mock_case_script] match-rate: {matches}/{n_valid} = {matches / n_valid * 100:.1f}%")
    print(f"[mock_case_script] p50: {_percentile(latencies_ms, 50):.0f}мс, p95: {_percentile(latencies_ms, 95):.0f}мс")
else:
    print("[mock_case_script] ни одного валидного ответа — метрики не посчитать")
PYEOF

if [[ "$FAIL" -ne 0 ]]; then
    printf '\n[mock_case_script] FAIL — были пустые/невалидные плоские ответы, см. выше\n' >&2
    exit 1
fi
printf '\n[mock_case_script] OK — механика подтверждена (multipart -> плоский JSON -> сравнение -> тайминг)\n'
exit 0
