#!/usr/bin/env bash
# Push the working tree to the VM over IAP, build the venv, switch release, restart.
#   ./deploy.sh              deploy code
#   ./deploy.sh --bootstrap  first time: also run infra/bootstrap-vm.sh on the VM
#   ./deploy.sh --rollback   point 'current' at the previous release and restart
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source infra/config.env

MODE="${1:-deploy}"
SSH=(gcloud compute ssh "$VM_NAME" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap)

if [[ "$MODE" == "--rollback" ]]; then
  # shellcheck disable=SC2016  # expanded on the VM, not locally
  "${SSH[@]}" --command 'sudo bash -euo pipefail -c "
    cd /opt/garmin-mcp/releases
    cur=\$(basename \$(readlink -f /opt/garmin-mcp/current))
    prev=\$(ls -1t | grep -vx \"\$cur\" | head -1)
    [[ -n \"\$prev\" ]] || { echo no previous release; exit 1; }
    ln -sfn /opt/garmin-mcp/releases/\$prev /opt/garmin-mcp/current
    systemctl restart garmin-mcp && echo rolled back to \$prev"'
  exit 0
fi

[[ -f uv.lock ]] || { echo "uv.lock missing; run 'uv lock'"; exit 1; }
REL="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || echo nogit)"
BUNDLE="$(mktemp -t garmin-mcp.XXXXXX).tgz"
trap 'rm -f "$BUNDLE"' EXIT

# Tracked + untracked-but-not-ignored files only (never .venv, .env, tokens).
git ls-files -co --exclude-standard -z | tar --null -czf "$BUNDLE" -T -
echo "==> Uploading release $REL"
gcloud compute scp "$BUNDLE" "$VM_NAME:/tmp/garmin-mcp-$REL.tgz" \
  --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap

"${SSH[@]}" --command "sudo env REL='$REL' MODE='$MODE' DOMAIN='$DOMAIN' \
  GITHUB_CLIENT_ID='$GITHUB_CLIENT_ID' ALLOWED_GITHUB_USERS='$ALLOWED_GITHUB_USERS' bash -s" <<'REMOTE'
set -euo pipefail
D=/opt/garmin-mcp/releases/$REL
mkdir -p "$D"
tar -xzf "/tmp/garmin-mcp-$REL.tgz" -C "$D"
rm -f "/tmp/garmin-mcp-$REL.tgz"
chown -R root:root "$D"
chmod -R go-w,a+rX "$D"

if [[ "$MODE" == "--bootstrap" ]]; then
  bash "$D/infra/bootstrap-vm.sh"
fi

echo "==> uv sync (frozen, no dev deps)"
cd "$D"
UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/var/cache/uv UV_PYTHON_DOWNLOADS=never \
  uv sync --frozen --no-dev --python 3.12 -q
chmod -R a+rX "$D/.venv"

# Pick up unit changes on every deploy.
if ! cmp -s "$D/infra/garmin-mcp.service" /etc/systemd/system/garmin-mcp.service; then
  install -m 0644 "$D/infra/garmin-mcp.service" /etc/systemd/system/garmin-mcp.service
  systemctl daemon-reload
fi

ln -sfn "$D" /opt/garmin-mcp/current
systemctl restart garmin-mcp
for i in $(seq 1 60); do  # e2-micro cold start can take ~30s
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "==> healthy: $REL"; break
  fi
  sleep 1
  if [[ $i == 60 ]]; then
    echo "!! service did not become healthy; recent logs:"
    journalctl -u garmin-mcp -n 40 --no-pager
    exit 1
  fi
done

# Keep the 3 newest releases.
cd /opt/garmin-mcp/releases && ls -1t | tail -n +4 | xargs -r rm -rf
REMOTE
