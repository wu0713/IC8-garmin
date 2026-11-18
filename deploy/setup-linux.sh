#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  跑課 Garmin-IC8：一鍵架站（Linux / 樹莓派 / Ubuntu / Debian）
#  做的事：裝 Docker + Tailscale → 啟動 App → 開 Tailscale Funnel 公開網址
#  可以重複執行（已經裝好的步驟會自動跳過）
#
#  用法：在專案資料夾裡執行
#      bash deploy/setup-linux.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8000

say()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔ %s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31m✘ %s\033[0m\n' "$*"; exit 1; }

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

# ── 0. 檢查系統 ──
say "檢查系統"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|aarch64|arm64) ok "CPU 架構 $ARCH" ;;
  armv7l|armv6l) die "偵測到 32 位元系統（$ARCH）。樹莓派請改裝 64 位元的 Raspberry Pi OS 再執行。" ;;
  *) warn "沒測試過的架構 $ARCH，繼續試試看" ;;
esac
command -v curl >/dev/null || { $SUDO apt-get update -qq && $SUDO apt-get install -y -qq curl; }

# ── 1. Docker ──
say "Docker"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  ok "已安裝：$(docker --version)"
else
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker
  ok "Docker 安裝完成，並設定開機自動啟動"
fi
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"

# ── 2. 設定檔 .env ──
say "設定檔 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  ok "已建立 .env"
fi
CUR="$(grep '^INVITE_CODE=' .env | cut -d= -f2- || true)"
if [ -z "$CUR" ] || [ "$CUR" = "換成你自己的邀請碼" ]; then
  CODE="run-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  sed -i "s|^INVITE_CODE=.*|INVITE_CODE=${CODE}|" .env
  ok "朋友註冊用的邀請碼：${CODE}"
else
  ok "保留原本的邀請碼"
fi
mkdir -p data

# ── 3. 啟動 App ──
say "建置並啟動 App（第一次大約 2–5 分鐘，樹莓派會比較久）"
$DOCKER compose up -d --build
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${PORT}/api/meta" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:${PORT}/api/meta" >/dev/null || die "App 沒有起來，請執行：$DOCKER compose logs"
ok "App 在 http://127.0.0.1:${PORT} 跑起來了（Docker 會讓它開機自動啟動）"

# ── 4. Tailscale ──
say "Tailscale"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | $SUDO sh
fi
$SUDO systemctl enable --now tailscaled >/dev/null 2>&1 || true
if ! $SUDO tailscale status >/dev/null 2>&1; then
  warn "接下來會出現一個網址，用瀏覽器打開並登入 Tailscale（Google / GitHub / Microsoft 帳號都可以）"
  $SUDO tailscale up
fi
ok "Tailscale 已登入"

# ── 5. Funnel：開公開網址 ──
say "開啟 Tailscale Funnel"
warn "如果下面出現「Funnel is not enabled」和一個網址：用瀏覽器打開、按同意，指令會自己繼續"
$SUDO tailscale funnel --bg "$PORT" || true
FSTAT="$($SUDO tailscale funnel status 2>&1 || true)"
echo "$FSTAT" | grep -q "https://" || { echo "$FSTAT"; die "Funnel 沒有開成功。開通後再執行一次這個腳本即可。"; }
ok "Funnel 已開啟（--bg 模式，重開機也會自動恢復）"

DNS="$($SUDO tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
[ -n "$DNS" ] || DNS="$(echo "$FSTAT" | grep -o 'https://[^ /]*' | head -1 | sed 's|https://||')"
URL="https://${DNS}"

# ── 6. 把網址寫回 .env（行事曆訂閱連結會用到） ──
if grep -q "^PUBLIC_BASE_URL=${URL}$" .env; then
  ok "PUBLIC_BASE_URL 已是 ${URL}"
else
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${URL}|" .env
  $DOCKER compose up -d >/dev/null
  ok "PUBLIC_BASE_URL 設為 ${URL}，App 已重新載入"
fi

sleep 3
if curl -fsS --max-time 15 "${URL}/api/meta" >/dev/null 2>&1; then
  ok "從外部網址測試連線成功"
else
  warn "外部網址暫時連不到。剛開通時 DNS 和憑證需要 1–10 分鐘，稍後用手機試試。"
fi

INVITE="$(grep '^INVITE_CODE=' .env | cut -d= -f2-)"
cat <<EOF

────────────────────────────────────────────
  🎉 完成！

  網址：   ${URL}
  邀請碼： ${INVITE}

  iPhone：用 Safari 打開網址 → 分享 → 加入主畫面
  朋友：   把網址和邀請碼傳給他們就好，不用裝任何東西

  常用指令（在這個資料夾裡執行）：
    看紀錄    $DOCKER compose logs -f
    重啟      $DOCKER compose restart
    升級套件  bash deploy/update.sh
    關閉公開  $SUDO tailscale funnel --bg $PORT off
────────────────────────────────────────────
EOF
