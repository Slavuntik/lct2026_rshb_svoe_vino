#!/usr/bin/env bash
# Переключение адреса «живого стенда» в документах — ПОДГОТОВЛЕНО заранее (задание тимлида
# 27.09), но НЕ ПРИМЕНЕНО: переключение стендов держим до вердикта ml-lead по 648ad43 и фикса
# залипания VLM у ml-engineer. Когда команда даст добро — один вызов применяет, один откатывает.
#
# Меняет РОВНО адрес (89.110.72.101 -> 46.243.211.32) в фиксированном списке файлов, ничего
# больше не трогает (без сопутствующих правок текста) — список согласован с тимлидом построчно.
# Инфраструктурные файлы (infra/ams3/*, infra/cpulab/*) сюда намеренно НЕ входят: они по
# определению про конкретный сервер, адрес там не «зашит», а является предметом документа.
#
# Использование:
#   infra/switch-address.sh status   — только показать, сколько раз старый/новый адрес
#                                       встречается в каждом файле списка (ничего не меняет)
#   infra/switch-address.sh apply    — ams3 -> cpulab по всему списку (высокий+средний приоритет)
#   infra/switch-address.sh revert   — cpulab -> ams3 (откат, тот же список)
#
# DRY_RUN=1 перед apply/revert — как status, но проходит через реальный код замены (лишняя
# проверка, что sed отработал бы без ошибок), файлов не трогает.
set -euo pipefail

OLD_IP="89.110.72.101"   # ams3 — старый/резервный адрес
NEW_IP="46.243.211.32"   # cpulab — новый основной адрес

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Высокий приоритет — видит жюри/участники (тимлид, задание 27.09).
HIGH=(
  "README.md"
  "docs/architecture/HLD.md"
  "docs/architecture/operations.md"
  "docs/product/requirements-check.md"
  "docs/product/slides-7-11.md"
  "docs/product/video-script.md"
  "agents/BOARD.md"
  "apps/api/app/main.py"
)
# Не было явно в списке тимлида, но тот же паттерн в том же каталоге, что HLD.md/operations.md
# (нашёл при исходном аудите, reports/devops-switch-prep.md) — добавил, отметьте, если не нужно.
HIGH_EXTRA=(
  "docs/architecture/README.md"
)
# Средний приоритет — внутренний тулинг команды, риска нет, применяем тем же вызовом
# отдельным куском (тимлид, задание 27.09).
MEDIUM=(
  ".claude/agents/devops.md"
  ".claude/agents/qa-manual.md"
  "design/ui-prototype/README.md"
  "design/ui-prototype/backend/README.md"
  "design/ui-prototype/web/README.md"
  "design/ui-prototype/web/vite.config.ts"
)

ACTION="${1:?использование: infra/switch-address.sh status|apply|revert}"

count_occurrences() {
  local n
  n="$( (grep -oF "$1" "$2" 2>/dev/null || true) | wc -l | tr -d ' ')"
  echo "$n"
}

replace_in_file() {
  local f="$1" from="$2" to="$3"
  [ -f "$f" ] || { echo "  ПРОПУЩЕН (нет файла): $f" >&2; return 1; }
  local before after tmp
  before="$(count_occurrences "$from" "$f")"
  if [ "$before" = "0" ]; then
    echo "  $f: 0 вхождений «$from» — нечего менять (уже применено или список устарел?)"
    return 0
  fi
  if [ -n "${DRY_RUN:-}" ]; then
    echo "  $f: DRY-RUN — заменил бы $before вхождений «$from» -> «$to»"
    return 0
  fi
  tmp="$(mktemp)"
  sed "s|$from|$to|g" "$f" > "$tmp" && mv "$tmp" "$f"
  after="$(count_occurrences "$to" "$f")"
  echo "  $f: заменено $before, теперь «$to» встречается $after раз"
}

show_status_line() {
  local f="$1"
  [ -f "$f" ] || { echo "  ПРОПУЩЕН (нет файла): $f" >&2; return; }
  echo "  $f: ams3=$(count_occurrences "$OLD_IP" "$f") cpulab=$(count_occurrences "$NEW_IP" "$f")"
}

echo "=== Высокий приоритет ==="
for f in "${HIGH[@]}" "${HIGH_EXTRA[@]}"; do
  case "$ACTION" in
    status) show_status_line "$f" ;;
    apply)  replace_in_file "$f" "$OLD_IP" "$NEW_IP" ;;
    revert) replace_in_file "$f" "$NEW_IP" "$OLD_IP" ;;
    *) echo "неизвестное действие: $ACTION (status|apply|revert)"; exit 1 ;;
  esac
done

echo "=== Средний приоритет ==="
for f in "${MEDIUM[@]}"; do
  case "$ACTION" in
    status) show_status_line "$f" ;;
    apply)  replace_in_file "$f" "$OLD_IP" "$NEW_IP" ;;
    revert) replace_in_file "$f" "$NEW_IP" "$OLD_IP" ;;
  esac
done

[ "$ACTION" = status ] || [ -n "${DRY_RUN:-}" ] || {
  echo
  echo "Готово. Не забыть: git diff (проверить руками) -> git add -u <файлы выше> ->"
  echo "git commit -m 'девопс/тимлид: адрес стенда -> ...' -- <файлы выше> (pathspec, не -A)."
}
