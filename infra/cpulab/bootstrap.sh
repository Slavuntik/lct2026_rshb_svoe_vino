#!/usr/bin/env bash
# Первичная настройка второго хак-стенда «cpulab» (запуск ОДИН раз, идемпотентен).
#
# В отличие от infra/ams3 (боевой VPN-бокс) этот сервер — ЧИСТАЯ выделенная машина:
# 8 vCPU, 15 ГБ RAM, 27 ГБ диска, Ubuntu 22.04.5, без GPU, БЕЗ конкурирующих служб.
# Ограничений ams3 (не трогать ufw/iptables/Docker/443/8444/59999) здесь НЕТ — но
# фаервол всё равно ставим по минимуму (см. блок ufw ниже: только 22 и 80).
#
# Отличие от infra/ams3/bootstrap.sh по пользователю: `somelye` здесь уже существует
# (обычный логин-пользователь с домашним каталогом /home/somelye и НЕограниченным
# passwordless sudo — так поднял сервер Вячеслав/провайдер), а не системный аккаунт,
# которого раньше не было. Поэтому здесь НЕТ useradd/sudoers.d — только каталоги
# /opt/somelye/* (владелец somelye) и установка пакетов через уже имеющийся sudo.
#
# Использование: bootstrap.sh   (без аргументов — публичный ключ CI не нужен: тот же
# ключ ~/.ssh/cpu_lab, которым тимлид уже даёт доступ, используется и для выкатов;
# отдельного CI-пользователя/ключа для этого стенда пока нет, см. README «Отличия»).
set -euo pipefail

APP_USER=somelye
BASE=/opt/somelye
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"   # рядом лежат unit, nginx-конфиг и шаблон env

[ "$(id -un)" = "$APP_USER" ] || { echo "запускать по SSH от пользователя $APP_USER (текущий: $(id -un))"; exit 1; }
sudo -n true 2>/dev/null || { echo "нужен passwordless sudo для $APP_USER — на этом сервере он уже должен быть"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
# libgl1/libglib2.0 — системные зависимости OpenCV (paddleocr тянет НЕ headless-сборку cv2:
# без них падает `ImportError: libGL.so.1`), libgomp1 — рантайм OpenMP для torch/onnxruntime.
sudo apt-get install -y -qq nginx rsync curl ca-certificates openssl libgl1 libgomp1 >/dev/null
sudo apt-get install -y -qq libglib2.0-0t64 >/dev/null 2>&1 || sudo apt-get install -y -qq libglib2.0-0 >/dev/null

sudo install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$BASE" "$BASE/app" "$BASE/web"
sudo install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$BASE/data" "$BASE/data/case" "$BASE/data/db" \
  "$BASE/data/fastembed" "$BASE/data/models" "$BASE/data/models/hf" "$BASE/data/models/paddlex" \
  "$BASE/data/scans" "$BASE/certs"

# uv — под текущим пользователем (его РЕАЛЬНЫЙ $HOME, /home/somelye, не /opt/somelye —
# в отличие от ams3, где системный пользователь имел --home-dir /opt/somelye). deploy.sh
# и остальные скрипты этого каталога поэтому используют "$HOME/.local/bin/uv", не
# зашитый /opt/somelye/.local/bin/uv — портативно между обоими стендами.
if [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
# Системный Python 3.10 НЕ трогаем — 3.12 ставится ИЗОЛИРОВАННО через uv (задание
# Вячеслава 27.09: "ставь интерпретатор через uv, системный не трогай").
"$HOME/.local/bin/uv" python install 3.12 >/dev/null

ENV_FILE="$BASE/somelye.env"
if [ ! -f "$ENV_FILE" ]; then
  install -m 600 "$SRC_DIR/somelye.env.example" "$ENV_FILE"
  echo "# JWT_SECRET/VISION_LLM_*/LLM_* переносятся с ams3 отдельным шагом (см. README, «Секреты»)." >> "$ENV_FILE"
fi

sudo install -m 644 "$SRC_DIR/somelye-api.service" /etc/systemd/system/somelye-api.service
sudo systemctl daemon-reload
sudo systemctl enable somelye-api >/dev/null 2>&1

sudo install -m 644 "$SRC_DIR/nginx-somelye.conf" /etc/nginx/sites-available/somelye
sudo ln -sf /etc/nginx/sites-available/somelye /etc/nginx/sites-enabled/somelye
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t -q
sudo systemctl enable nginx >/dev/null 2>&1
sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx

# Фаервол (задание 27.09: "это НЕ VPN-бокс... но фаервол всё равно настрой аккуратно и
# опиши, что открыл"). До этой команды заняты были только 22 (ssh) и 53 (systemd-resolved,
# 127.0.0.53, ТОЛЬКО loopback — наружу не торчит, не трогаем). Открываем РОВНО две вещи:
# 22/tcp (ssh — ПЕРВЫМ, чтобы не отрезать себя) и 80/tcp (nginx, статика+API). Порт 8000
# (uvicorn) слушает только 127.0.0.1 (см. somelye-api.service ExecStart) — снаружи и так
# недостижим, отдельного правила не требует. Никакого TLS/443 — как на ams3, нет домена.
sudo ufw allow 22/tcp comment 'ssh' >/dev/null
sudo ufw allow 80/tcp comment 'nginx: web + /v1 API' >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status verbose

echo "bootstrap OK · $(uname -m) · $(nproc) vCPU · $(free -h --si 2>/dev/null | awk '/Mem:/{print $2}') RAM · без swap (диск 27ГБ, RAM с запасом ×~4 к замеренному пику ams3 3.9ГБ)"
