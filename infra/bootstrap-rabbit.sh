#!/usr/bin/env bash
# One-time setup for the Rabbit web app service. Run ON the VM as root from an
# unpacked release dir (deploy.sh --rabbit --bootstrap does this). Requires the
# main bootstrap (Caddy, uv, Python) to have run already. Never touches the
# garmin-mcp service.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${DOMAIN:-}"

echo "==> rabbit user and directories"
id rabbit >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/rabbit --shell /usr/sbin/nologin rabbit
install -d -m 0700 -o rabbit -g rabbit /var/lib/rabbit /var/lib/rabbit/tokens
install -d -m 0755 -o root -g root /opt/rabbit /opt/rabbit/releases
install -d -m 0750 -o root -g rabbit /etc/rabbit

echo "==> Config /etc/rabbit/env"
if [[ ! -f /etc/rabbit/env ]]; then
  PROJECT_ID=$(curl -fsS -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/project/project-id || true)
  sed -e "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${DOMAIN:-pending.invalid}|" \
      -e "s|^GCP_PROJECT=.*|GCP_PROJECT=${PROJECT_ID}|" \
      "$SRC/infra/rabbit.env.example" > /etc/rabbit/env
fi
chown root:rabbit /etc/rabbit/env
chmod 0640 /etc/rabbit/env

echo "==> systemd unit"
install -m 0644 "$SRC/infra/rabbit.service" /etc/systemd/system/rabbit.service
systemctl daemon-reload
systemctl enable rabbit

if [[ -n "$DOMAIN" ]]; then
  DOMAIN="$DOMAIN" bash "$SRC/infra/render-caddy.sh"
fi
echo "Rabbit bootstrap complete. Fill in /etc/rabbit/env (README), then ./deploy.sh --rabbit."
