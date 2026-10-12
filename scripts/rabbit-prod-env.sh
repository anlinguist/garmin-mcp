#!/usr/bin/env bash
# Fill /etc/rabbit/env on the VM (after ./deploy.sh --rabbit --bootstrap), then restart rabbit.
# Usage: scripts/rabbit-prod-env.sh <google_client_id> <allowed_google_users> <vapid_subject>
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source infra/config.env
# shellcheck disable=SC1091
source infra/gcloud-python.sh
CID="$1" USERS="$2" VAPID="$3"
gcloud compute ssh "$VM_NAME" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap --command "sudo bash -euo pipefail -c '
  f=/etc/rabbit/env
  sed -i -e \"s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://$DOMAIN|\" \
         -e \"s|^GOOGLE_CLIENT_ID=.*|GOOGLE_CLIENT_ID=$CID|\" \
         -e \"s|^ALLOWED_GOOGLE_USERS=.*|ALLOWED_GOOGLE_USERS=$USERS|\" \
         -e \"s|^VAPID_SUBJECT=.*|VAPID_SUBJECT=$VAPID|\" \$f
  grep -E \"^(PUBLIC_BASE_URL|GCP_PROJECT|GOOGLE_CLIENT_ID|ALLOWED_GOOGLE_USERS|VAPID_SUBJECT)=\" \$f
  systemctl restart rabbit
  for i in \$(seq 1 60); do curl -fsS http://127.0.0.1:8001/healthz >/dev/null 2>&1 && { echo rabbit healthy; exit 0; }; sleep 1; done
  journalctl -u rabbit -n 40 --no-pager; exit 1'"
