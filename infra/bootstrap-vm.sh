#!/usr/bin/env bash
# One-time VM setup. Run ON the VM as root from an unpacked release dir, e.g.
#   sudo DOMAIN=garmin.example.com GITHUB_CLIENT_ID=... ALLOWED_GITHUB_USERS=... bash infra/bootstrap-vm.sh
# deploy.sh --bootstrap does this for you.
set -euo pipefail
DOMAIN="${DOMAIN:-}"
[[ "$DOMAIN" == PENDING* ]] && DOMAIN=""
SRC="$(cd "$(dirname "$0")/.." && pwd)"
UV_VERSION=0.12.21
PY_VERSION=3.12

export DEBIAN_FRONTEND=noninteractive

echo "==> Packages"
apt-get update -q
apt-get install -y -q curl ca-certificates gnupg debian-keyring debian-archive-keyring \
  apt-transport-https unattended-upgrades
# Security updates applied automatically.
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> 1G swapfile (headroom for e2-micro's 1GB RAM during uv sync)"
if [[ ! -f /swapfile ]]; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
fi

echo "==> Caddy (official Cloudsmith repo)"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
    gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q
  apt-get install -y -q caddy
fi

echo "==> uv ${UV_VERSION} + Python ${PY_VERSION}"
if ! command -v uv >/dev/null || [[ "$(uv --version | awk '{print $2}')" != "$UV_VERSION" ]]; then
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" |
    env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
install -d -m 0755 /opt/uv /opt/uv/python
UV_PYTHON_INSTALL_DIR=/opt/uv/python uv python install "$PY_VERSION"
chmod -R a+rX /opt/uv/python

echo "==> Service user and directories"
id garmin-mcp >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/garmin-mcp --shell /usr/sbin/nologin garmin-mcp
install -d -m 0700 -o garmin-mcp -g garmin-mcp /var/lib/garmin-mcp /var/lib/garmin-mcp/tokens /var/lib/garmin-mcp/oauth
install -d -m 0755 -o root -g root /opt/garmin-mcp /opt/garmin-mcp/releases
install -d -m 0750 -o root -g garmin-mcp /etc/garmin-mcp

echo "==> Config /etc/garmin-mcp/env"
if [[ ! -f /etc/garmin-mcp/env ]]; then
  PROJECT_ID=$(curl -fsS -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/project/project-id || true)
  sed -e "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${DOMAIN:-pending.invalid}|" \
      -e "s|^GCP_PROJECT=.*|GCP_PROJECT=${PROJECT_ID}|" \
      -e "s|^GITHUB_CLIENT_ID=.*|GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID:-}|" \
      -e "s|^ALLOWED_GITHUB_USERS=.*|ALLOWED_GITHUB_USERS=${ALLOWED_GITHUB_USERS:-}|" \
      "$SRC/.env.example" > /etc/garmin-mcp/env
fi
chown root:garmin-mcp /etc/garmin-mcp/env
chmod 0640 /etc/garmin-mcp/env

echo "==> systemd unit + Caddyfile"
install -m 0644 "$SRC/infra/garmin-mcp.service" /etc/systemd/system/garmin-mcp.service
systemctl daemon-reload
systemctl enable garmin-mcp
if [[ -n "$DOMAIN" ]]; then
  TLS_LINE=""
  if [[ -f /etc/caddy/certs/origin.pem && -f /etc/caddy/certs/origin.key ]]; then
    TLS_LINE="tls /etc/caddy/certs/origin.pem /etc/caddy/certs/origin.key"
  fi
  sed -e "s|__DOMAIN__|${DOMAIN}|g" -e "s|__TLS__|${TLS_LINE}|" "$SRC/infra/Caddyfile" > /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  systemctl reload-or-restart caddy
else
  echo "!! DOMAIN not set yet: leaving Caddy's default config; re-run with DOMAIN=... later."
fi

echo "==> Login helper /usr/local/sbin/garmin-mcp-login"
cat > /usr/local/sbin/garmin-mcp-login <<'EOF'
#!/usr/bin/env bash
# Runs the Garmin login CLI as the service user with the service's config.
set -euo pipefail
exec sudo -u garmin-mcp bash -c 'set -a; . /etc/garmin-mcp/env; set +a; umask 077; \
  exec /opt/garmin-mcp/current/.venv/bin/garmin-mcp-login "$@"' _ "$@"
EOF
chmod 0755 /usr/local/sbin/garmin-mcp-login

echo "Bootstrap complete. Review /etc/garmin-mcp/env, then deploy + log in (README)."
