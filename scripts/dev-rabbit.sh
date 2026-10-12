#!/usr/bin/env bash
# Run the Rabbit web app locally, separate from any MCP server (MCP_ENABLED=false, :8001).
#
#   scripts/dev-rabbit.sh mac     Mac only: http://localhost:5173/app/ (Vite hot reload),
#                                 no Google sign-in (DEV_NO_AUTH_USER=andrew).
#   scripts/dev-rabbit.sh phone   Phone over HTTPS through a Cloudflare Tunnel to
#                                 $RABBIT_DEV_URL (e.g. https://rabbit-dev.example.com):
#                                 real Google sign-in + Web Push; frontend rebuilds on save.
#
# Local state (keys, sessions, push subscriptions) lives in ./data/rabbit-dev (gitignored).
# Phone mode reads GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ALLOWED_GOOGLE_USERS, RABBIT_DEV_URL
# and CLOUDFLARE_TUNNEL from ./data/rabbit-dev/phone.env.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-mac}"
DIR=data/rabbit-dev
mkdir -p "$DIR" && chmod 700 data "$DIR"

# Stable local keys so sessions and push subscriptions survive restarts.
KEYS="$DIR/keys.env"
if [[ ! -f "$KEYS" ]]; then
  ( umask 077
    echo "GARMIN_MCP_FERNET_KEY=$(uv run -q python -c 'from garmin_mcp.tokens import generate_key; print(generate_key())')"
    echo "VAPID_PRIVATE_KEY=$(uv run -q python -c 'from garmin_mcp.webpush import generate_vapid_key; print(generate_vapid_key())')"
  ) > "$KEYS"
fi
set -a; . "$KEYS"; set +a
export MCP_ENABLED=false APP_ENABLED=true PORT=8001 HOST=127.0.0.1 DATA_DIR="$DIR"
export VAPID_SUBJECT="${VAPID_SUBJECT:-mailto:dev@localhost.invalid}" LOG_LEVEL=INFO

pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT INT TERM

(cd web && bun install --silent)

case "$MODE" in
  mac)
    export PUBLIC_BASE_URL=http://localhost:8001 DEV_NO_AUTH_USER="${DEV_NO_AUTH_USER:-andrew}"
    uv run garmin-mcp & pids+=($!)
    echo "==> Rabbit: http://localhost:5173/app/  (signed in as $DEV_NO_AUTH_USER; Ctrl-C to stop)"
    (cd web && bun run dev) & pids+=($!)
    ;;
  phone)
    [[ -f "$DIR/phone.env" ]] || { echo "Create $DIR/phone.env first (see README: Rabbit local dev)"; exit 1; }
    set -a; . "$DIR/phone.env"; set +a
    : "${RABBIT_DEV_URL:?}" "${GOOGLE_CLIENT_ID:?}" "${GOOGLE_CLIENT_SECRET:?}" "${ALLOWED_GOOGLE_USERS:?}" "${CLOUDFLARE_TUNNEL:?}"
    command -v cloudflared >/dev/null || { echo "Install cloudflared: brew install cloudflared"; exit 1; }
    export PUBLIC_BASE_URL="$RABBIT_DEV_URL"
    unset DEV_NO_AUTH_USER  # publicly reachable: real Google sign-in only
    (cd web && bun run build >/dev/null)
    (cd web && bunx vite build --watch >/dev/null 2>&1) & pids+=($!)
    uv run garmin-mcp & pids+=($!)
    cloudflared tunnel --no-autoupdate run --url http://127.0.0.1:8001 "$CLOUDFLARE_TUNNEL" & pids+=($!)
    echo "==> Rabbit: $RABBIT_DEV_URL/app/  (frontend rebuilds on save; refresh the phone; Ctrl-C to stop)"
    ;;
  *) echo "usage: $0 mac|phone"; exit 2 ;;
esac
wait
