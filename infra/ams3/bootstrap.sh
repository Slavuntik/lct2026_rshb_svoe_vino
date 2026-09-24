#!/usr/bin/env bash
# Первичная настройка хак-стенда на exit-ams3 (запуск ОДИН раз, от root; идемпотентен).
#
# ams3 — боевой VPN-выход (Reality :443/tcp, hysteria2 :8444/udp, WireGuard wg0 :59999/udp,
# реплика control-plane на 127.0.0.1:8090). Поэтому здесь НАМЕРЕННО нет:
#   - Docker (ставит политику FORWARD=DROP и ломает форвардинг WireGuard-клиентов);
#   - ufw/iptables/nftables (любое правило рискует отрезать VPN);
#   - портов 443 и 8444 (заняты VPN) — стенд слушает только :80.
# Память стенда ограничена systemd (MemoryMax), чтобы при пике OOM бил по стенду, а не по VPN.
#
# Использование: bootstrap.sh "<публичный SSH-ключ CI для пользователя somelye>"
set -euo pipefail

CI_PUBKEY="${1:?нужен публичный SSH-ключ CI первым аргументом}"
APP_USER=somelye
BASE=/opt/somelye
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"   # рядом лежат unit, nginx-конфиг и шаблон env

[ "$(id -u)" -eq 0 ] || { echo "запускать от root"; exit 1; }
# uv ищет uv.toml в рабочей папке и выше — из /root пользователь somelye его не прочтёт.
cd /

for port in 443 8444; do
  ss -tulpnH | grep -q ":$port " || echo "ВНИМАНИЕ: порт $port не слушается — VPN на месте?"
done
if ss -tlpnH | grep -q ':80 ' && ! ss -tlpnH | grep ':80 ' | grep -q nginx; then
  echo "порт 80 занят не nginx — стоп"; exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# libgl1/libglib2.0 — системные зависимости OpenCV (paddleocr тянет НЕ headless-сборку cv2:
# без них падает `ImportError: libGL.so.1`), libgomp1 — рантайм OpenMP для torch.
apt-get install -y -qq nginx rsync curl ca-certificates openssl libgl1 libgomp1 >/dev/null
apt-get install -y -qq libglib2.0-0t64 >/dev/null 2>&1 || apt-get install -y -qq libglib2.0-0 >/dev/null

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$BASE" --shell /bin/bash "$APP_USER"
fi
install -d -o "$APP_USER" -g "$APP_USER" -m 755 "$BASE" "$BASE/app" "$BASE/web"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$BASE/data" "$BASE/data/cv" "$BASE/data/rag" \
  "$BASE/data/case" "$BASE/data/db" "$BASE/data/fastembed" "$BASE/data/models" \
  "$BASE/data/models/hf" "$BASE/data/models/paddlex"
install -d -o "$APP_USER" -g "$APP_USER" -m 700 "$BASE/.ssh"
touch "$BASE/.ssh/authorized_keys"
grep -qF "$CI_PUBKEY" "$BASE/.ssh/authorized_keys" || echo "$CI_PUBKEY" >> "$BASE/.ssh/authorized_keys"
chown "$APP_USER:$APP_USER" "$BASE/.ssh/authorized_keys"; chmod 600 "$BASE/.ssh/authorized_keys"

if ! swapon --show | grep -q .; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=10
  echo 'vm.swappiness=10' > /etc/sysctl.d/90-somelye-swap.conf
fi

if [ ! -x "$BASE/.local/bin/uv" ]; then
  sudo -u "$APP_USER" -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' >/dev/null
fi
sudo -u "$APP_USER" -H "$BASE/.local/bin/uv" python install 3.12 >/dev/null

ENV_FILE="$BASE/somelye.env"
if [ ! -f "$ENV_FILE" ]; then
  install -o "$APP_USER" -g "$APP_USER" -m 600 "$SRC_DIR/somelye.env.example" "$ENV_FILE"
  echo "JWT_SECRET=$(openssl rand -hex 32)" >> "$ENV_FILE"
fi

install -m 644 "$SRC_DIR/somelye-api.service" /etc/systemd/system/somelye-api.service
systemctl daemon-reload
systemctl enable somelye-api >/dev/null 2>&1

install -m 644 "$SRC_DIR/nginx-somelye.conf" /etc/nginx/sites-available/somelye
ln -sf /etc/nginx/sites-available/somelye /etc/nginx/sites-enabled/somelye
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx

cat > /etc/sudoers.d/somelye <<SUDO
$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart somelye-api, /usr/bin/systemctl is-active somelye-api, /usr/bin/systemctl status somelye-api --no-pager
SUDO
chmod 440 /etc/sudoers.d/somelye
visudo -cf /etc/sudoers.d/somelye >/dev/null

echo "bootstrap OK · $(uname -m) · $(nproc) vCPU · swap $(swapon --show --noheadings | awk '{print $3}')"
