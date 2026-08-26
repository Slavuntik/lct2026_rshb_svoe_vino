#!/usr/bin/env bash
# infra/scripts/validate.sh
#
# Статическая валидация инфра-комплекта БЕЗ Docker/nginx на машине разработки:
#   1) все *.yml/*.yaml под infra/ и .github/workflows/ — валидный YAML (python3 + PyYAML);
#   2) все *.sh под infra/scripts/ — валидный bash-синтаксис (bash -n);
#   3) обязательные файлы комплекта присутствуют;
#   4. .env.*.example не содержат похожих на реальные секреты значений (эвристика);
#   5) nginx.conf — грубая проверка баланса фигурных скобок (не замена `nginx -t`).
#
# Запуск: infra/scripts/validate.sh
# Код возврата: 0 — всё зелёное, 1 — есть ошибки (список — в stdout).

set -uo pipefail

# Корень репозитория = на два уровня выше этого скрипта (infra/scripts/validate.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

FAIL=0
PASS_COUNT=0
FAIL_COUNT=0

ok() {
    printf '  OK   %s\n' "$1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

bad() {
    printf '  FAIL %s\n' "$1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL=1
}

section() {
    printf '\n== %s ==\n' "$1"
}

# --- 0. Python3 доступен? Без него шаг 1 невозможен. ---------------------
if ! command -v python3 >/dev/null 2>&1; then
    printf 'FAIL нет python3 в PATH — валидация YAML невозможна\n'
    exit 1
fi

PY_HAS_YAML=1
python3 -c "import yaml" >/dev/null 2>&1 || PY_HAS_YAML=0
if [[ "${PY_HAS_YAML}" -eq 0 ]]; then
    printf 'Нет модуля PyYAML — ставлю временно во venv для валидации (без записи в проект).\n'
    TMP_VENV="$(mktemp -d)"
    python3 -m venv "${TMP_VENV}" >/dev/null 2>&1
    # shellcheck disable=SC1091
    source "${TMP_VENV}/bin/activate"
    pip install --quiet pyyaml >/dev/null 2>&1
    PY_HAS_YAML=1
    trap 'deactivate >/dev/null 2>&1; rm -rf "${TMP_VENV}"' EXIT
fi

# --- 1. YAML-валидация -----------------------------------------------------
section "YAML-синтаксис (infra/**/*.yml, .github/workflows/*.yml)"

# Не mapfile/readarray: macOS до сих пор поставляет bash 3.2 (лицензия GPLv3), где их нет —
# этот скрипт должен зелёно отрабатывать именно на такой машине (см. agents/E-infra.md).
YAML_FILES=()
while IFS= read -r f; do
    YAML_FILES+=("$f")
done < <(find infra .github/workflows -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null | sort)

if [[ "${#YAML_FILES[@]}" -eq 0 ]]; then
    bad "не найдено ни одного .yml/.yaml файла — комплект не может быть пустым"
else
    for f in "${YAML_FILES[@]}"; do
        if python3 -c "
import sys, yaml
with open(sys.argv[1], encoding='utf-8') as fh:
    docs = list(yaml.safe_load_all(fh))
if not docs or docs == [None]:
    print('пустой документ', file=sys.stderr)
    sys.exit(1)
" "$f" 2>/tmp/validate_yaml_err.$$; then
            ok "$f"
        else
            bad "$f — $(cat /tmp/validate_yaml_err.$$ 2>/dev/null | tr '\n' ' ')"
        fi
        rm -f /tmp/validate_yaml_err.$$
    done
fi

# --- 2. docker compose config (если установлен docker) --------------------
section "docker compose config --quiet (best-effort, docker на этой машине не ожидается)"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    for cf in infra/compose.prod.yml infra/compose.staging.yml; do
        if [[ -f "$cf" ]]; then
            if docker compose -f "$cf" config --quiet 2>/tmp/validate_dc_err.$$; then
                ok "$cf (docker compose config)"
            else
                bad "$cf — docker compose config: $(cat /tmp/validate_dc_err.$$ | tr '\n' ' ')"
            fi
            rm -f /tmp/validate_dc_err.$$
        fi
    done
else
    printf '  SKIP docker/docker-compose недоступны на этой машине — валидировали только YAML-синтаксис\n'
fi

# --- 3. bash-синтаксис скриптов --------------------------------------------
section "bash -n (infra/scripts/*.sh)"

SH_FILES=()
while IFS= read -r f; do
    SH_FILES+=("$f")
done < <(find infra/scripts -type f -name '*.sh' 2>/dev/null | sort)

if [[ "${#SH_FILES[@]}" -eq 0 ]]; then
    bad "infra/scripts/ пуст — ожидались backup.sh/restore.sh/deploy.sh/validate.sh"
else
    for f in "${SH_FILES[@]}"; do
        if bash -n "$f" 2>/tmp/validate_sh_err.$$; then
            ok "$f"
        else
            bad "$f — $(cat /tmp/validate_sh_err.$$ | tr '\n' ' ')"
        fi
        rm -f /tmp/validate_sh_err.$$
        # У каждого скрипта должен быть +x бит — иначе deploy на хосте молча упадёт.
        if [[ ! -x "$f" ]]; then
            bad "$f — не исполняемый (chmod +x)"
        fi
    done
fi

# --- 4. Обязательные файлы комплекта ---------------------------------------
section "Обязательные файлы"

REQUIRED_FILES=(
    "infra/compose.prod.yml"
    "infra/compose.staging.yml"
    "infra/Dockerfile.api"
    "infra/nginx.conf"
    "infra/RUNBOOK.md"
    "infra/.env.prod.example"
    "infra/.env.staging.example"
    "infra/postgres/init/10-extensions.sql"
    "infra/scripts/backup.sh"
    "infra/scripts/restore.sh"
    "infra/scripts/deploy.sh"
    "infra/scripts/validate.sh"
    ".github/workflows/ci.yml"
    ".github/workflows/deploy.yml"
)

for f in "${REQUIRED_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        ok "$f присутствует"
    else
        bad "$f ОТСУТСТВУЕТ"
    fi
done

# --- 5. .env.*.example не должны содержать заполненных секретов -----------
section "Плейсхолдеры вместо секретов в .env.*.example"

for f in infra/.env.prod.example infra/.env.staging.example; do
    [[ -f "$f" ]] || continue
    # Ищем строки KEY=значение, где значение непустое, не плейсхолдер-заглушка
    # и не является заведомо безопасным (числа, url без креда, enum-значения).
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        case "$key" in
            *KEY*|*SECRET*|*PASSWORD*|*TOKEN*)
                if [[ -n "$val" ]] && [[ ! "$val" =~ ^(CHANGE_ME|__|\<).*$ ]]; then
                    bad "$f: ${key} похоже на заполненный секрет ('${val}') — должен быть плейсхолдер"
                fi
                ;;
        esac
    done < "$f"
    ok "$f проверен на плейсхолдеры"
done

# --- 6. nginx.conf — баланс скобок (не замена nginx -t) --------------------
section "nginx.conf — баланс { } (грубая проверка, nginx -t недоступен)"

NGINX_CONF="infra/nginx.conf"
if [[ -f "$NGINX_CONF" ]]; then
    OPEN=$(tr -cd '{' < "$NGINX_CONF" | wc -c | tr -d ' ')
    CLOSE=$(tr -cd '}' < "$NGINX_CONF" | wc -c | tr -d ' ')
    if [[ "$OPEN" -eq "$CLOSE" ]] && [[ "$OPEN" -gt 0 ]]; then
        ok "nginx.conf: { }: ${OPEN}/${CLOSE} совпадают"
    else
        bad "nginx.conf: { }: ${OPEN} открывающих / ${CLOSE} закрывающих — не совпадает"
    fi
    # каждая строка ssl_certificate должна ссылаться на путь внутри контейнера, не на реальный секрет
    if grep -qE 'ssl_certificate(_key)?\s+/etc/nginx/tls/' "$NGINX_CONF"; then
        ok "nginx.conf: TLS-пути указывают на смонтированный /etc/nginx/tls/ (не на секрет в репо)"
    else
        bad "nginx.conf: не найдены ожидаемые ssl_certificate-директивы на /etc/nginx/tls/"
    fi
else
    bad "infra/nginx.conf отсутствует"
fi

# --- Итог -------------------------------------------------------------------
section "Итог"
printf 'Пройдено: %d, провалено: %d\n' "$PASS_COUNT" "$FAIL_COUNT"

if [[ "$FAIL" -ne 0 ]]; then
    printf '\nVALIDATE: FAIL\n'
    exit 1
fi

printf '\nVALIDATE: OK\n'
exit 0
