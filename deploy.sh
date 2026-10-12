#!/usr/bin/env bash
# Push the working tree to the VM over IAP, build the venv, switch release, restart.
#   ./deploy.sh                      MCP server (garmin-mcp.service, /opt/garmin-mcp)
#   ./deploy.sh --bootstrap          first time: also run infra/bootstrap-vm.sh on the VM
#   ./deploy.sh --rollback           point 'current' at the previous release and restart
#   ./deploy.sh --rabbit [...]       same, for the Rabbit web app (rabbit.service, /opt/rabbit).
#                                    Never restarts the MCP server. --rabbit --bootstrap runs
#                                    infra/bootstrap-rabbit.sh (and re-renders the Caddyfile).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source infra/config.env
# IAP uploads are slow unless gcloud's Python has NumPy. Use a dedicated venv when one
# exists (uv venv ~/.local/share/gcloud-python && uv pip install numpy), not this repo's .venv.
if [[ -z "${CLOUDSDK_PYTHON:-}" && -x "$HOME/.local/share/gcloud-python/bin/python" ]]; then
  export CLOUDSDK_PYTHON="$HOME/.local/share/gcloud-python/bin/python"
fi

APP=garmin-mcp PORT=8000 MODE=deploy
for arg in "$@"; do
  case "$arg" in
    --rabbit) APP=rabbit PORT=8001 ;;
    --bootstrap|--rollback) MODE="$arg" ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done
SSH=(gcloud compute ssh "$VM_NAME" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap)

if [[ "$MODE" == "--rollback" ]]; then
  # shellcheck disable=SC2016  # expanded on the VM, not locally
  "${SSH[@]}" --command "sudo APP=$APP bash -euo pipefail -c '
    cd /opt/\$APP/releases
    cur=\$(basename \$(readlink -f /opt/\$APP/current))
    prev=\$(ls -1t | grep -vx \"\$cur\" | head -1)
    [[ -n \"\$prev\" ]] || { echo no previous release; exit 1; }
    ln -sfn /opt/\$APP/releases/\$prev /opt/\$APP/current
    systemctl restart \$APP && echo rolled back \$APP to \$prev'"
  exit 0
fi

[[ -f uv.lock ]] || { echo "uv.lock missing; run 'uv lock'"; exit 1; }
REL="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
BUNDLE="$(mktemp -t garmin-mcp.XXXXXX).tgz"
trap 'rm -f "$BUNDLE"' EXIT

if [[ "$APP" == rabbit ]]; then
  # Build the frontend locally (the VM never runs Node). The output is gitignored,
  # so it is added to the bundle explicitly below.
  echo "==> Building web app"
  command -v bun >/dev/null || { echo "bun is required to build web/ (https://bun.sh)"; exit 1; }
  (cd web && bun install --frozen-lockfile >/dev/null && bun run build >/dev/null)
  [[ -f src/garmin_mcp/web_dist/index.html ]] || { echo "web build produced no index.html"; exit 1; }
fi

# Tracked + untracked-but-not-ignored files only (never .venv, .env, tokens),
# plus the built frontend.
{ git ls-files -co --exclude-standard -z
  if [[ "$APP" == rabbit ]]; then find src/garmin_mcp/web_dist -type f -print0; fi
} | COPYFILE_DISABLE=1 tar --no-xattrs --null -czf "$BUNDLE" -T -
echo "==> Uploading $APP release $REL"
gcloud compute scp "$BUNDLE" "$VM_NAME:/tmp/$APP-$REL.tgz" \
  --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap

"${SSH[@]}" --command "sudo env APP='$APP' PORT='$PORT' REL='$REL' MODE='$MODE' DOMAIN='$DOMAIN' \
  GITHUB_CLIENT_ID='$GITHUB_CLIENT_ID' ALLOWED_GITHUB_USERS='$ALLOWED_GITHUB_USERS' bash -s" <<'REMOTE'
set -euo pipefail
D=/opt/$APP/releases/$REL
mkdir -p "$D"
tar -xzf "/tmp/$APP-$REL.tgz" -C "$D"
rm -f "/tmp/$APP-$REL.tgz"
chown -R root:root "$D"
chmod -R go-w,a+rX "$D"

if [[ "$MODE" == "--bootstrap" && "$APP" == garmin-mcp ]]; then
  bash "$D/infra/bootstrap-vm.sh"
elif [[ "$MODE" == "--bootstrap" ]]; then
  bash "$D/infra/bootstrap-rabbit.sh"
fi

echo "==> uv sync (frozen, no dev deps)"
cd "$D"
UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/var/cache/uv UV_PYTHON_DOWNLOADS=never \
  uv sync --frozen --no-dev --python 3.12 -q
chmod -R a+rX "$D/.venv"

# Pick up unit changes on every deploy.
if ! cmp -s "$D/infra/$APP.service" "/etc/systemd/system/$APP.service"; then
  install -m 0644 "$D/infra/$APP.service" "/etc/systemd/system/$APP.service"
  systemctl daemon-reload
fi

ln -sfn "$D" "/opt/$APP/current"
systemctl restart "$APP"
for i in $(seq 1 60); do  # e2-micro cold start can take ~30s
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "==> $APP healthy: $REL"; break
  fi
  sleep 1
  if [[ $i == 60 ]]; then
    echo "!! service did not become healthy; recent logs:"
    journalctl -u "$APP" -n 40 --no-pager
    exit 1
  fi
done

# Keep the 3 newest releases.
cd "/opt/$APP/releases" && ls -1t | tail -n +4 | xargs -r rm -rf
REMOTE
