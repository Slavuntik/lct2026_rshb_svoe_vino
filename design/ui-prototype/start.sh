#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1; then
  echo "Нужен Node.js: https://nodejs.org"
  exit 1
fi

if [ ! -d node_modules ]; then
  npm install
fi

echo "Сайт: http://localhost:5173"
echo "Локальный API (сомелье, /eval): http://localhost:3001"
echo "Скан и карточка идут на стенд через прокси фронта."
exec npm run dev
