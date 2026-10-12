#!/usr/bin/env bash
# Render infra/Caddyfile for $DOMAIN, validate, and reload Caddy (graceful: open
# connections to the MCP server are kept). Run ON the VM as root.
#   sudo DOMAIN=garmin.example.com bash infra/render-caddy.sh
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${DOMAIN:?DOMAIN is required}"
TLS_LINE=""
if [[ -f /etc/caddy/certs/origin.pem && -f /etc/caddy/certs/origin.key ]]; then
  TLS_LINE="tls /etc/caddy/certs/origin.pem /etc/caddy/certs/origin.key"
fi
TMP=$(mktemp)
sed -e "s|__DOMAIN__|${DOMAIN}|g" -e "s|__TLS__|${TLS_LINE}|" "$SRC/infra/Caddyfile" > "$TMP"
caddy validate --config "$TMP" --adapter caddyfile
install -m 0644 "$TMP" /etc/caddy/Caddyfile
rm -f "$TMP"
systemctl reload-or-restart caddy
echo "==> Caddy config applied for $DOMAIN"
