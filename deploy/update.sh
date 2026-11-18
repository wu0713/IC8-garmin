#!/usr/bin/env bash
# 升級到最新版 garminconnect（Garmin 改登入方式、同步一直失敗時執行這個）
set -euo pipefail
cd "$(dirname "$0")/.."
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
cp -n data/ic8.sqlite3 "data/ic8.sqlite3.bak-$(date +%Y%m%d)" 2>/dev/null || true
$DOCKER compose build --no-cache --pull
$DOCKER compose up -d
$DOCKER compose exec -T ic8 pip show garminconnect | grep -i '^version'
echo "✔ 已升級並重新啟動（資料庫備份在 data/ 裡）"
