#!/usr/bin/env bash
# One-time GCP setup. Run from your machine: ./infra/setup.sh
# Idempotent-ish: existing resources are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source infra/config.env

SA_NAME=garmin-mcp-vm
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
FERNET_SECRET=garmin-mcp-fernet-key
GH_SECRET=garmin-mcp-github-client-secret
IP_NAME=garmin-mcp-ip
WEB_TAG=garmin-mcp-web
SSH_TAG=garmin-mcp-iap-ssh

g() { gcloud --project "$PROJECT" "$@"; }
exists() { "$@" >/dev/null 2>&1; }

echo "==> Enabling APIs"
g services enable compute.googleapis.com secretmanager.googleapis.com iap.googleapis.com

echo "==> Service account (no project-level roles)"
exists g iam service-accounts describe "$SA" ||
  g iam service-accounts create "$SA_NAME" --display-name="garmin-mcp VM"

echo "==> Secrets"
if ! exists g secrets describe "$FERNET_SECRET"; then
  # 32 random bytes, urlsafe base64 = a Fernet key. Generated locally, piped straight in.
  python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode(),end="")' |
    g secrets create "$FERNET_SECRET" --replication-policy=automatic --data-file=-
fi
if ! exists g secrets describe "$GH_SECRET"; then
  # Created empty; add the value once the GitHub OAuth App exists (see README).
  g secrets create "$GH_SECRET" --replication-policy=automatic
fi
for s in "$FERNET_SECRET" "$GH_SECRET"; do
  # Per-secret binding: the VM can read exactly these two secrets and nothing else.
  g secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
done

echo "==> Static IP"
exists g compute addresses describe "$IP_NAME" --region "$REGION" ||
  g compute addresses create "$IP_NAME" --region "$REGION"
IP=$(g compute addresses describe "$IP_NAME" --region "$REGION" --format='value(address)')

echo "==> Firewall"
if [[ "${BEHIND_CLOUDFLARE:-false}" == "true" ]]; then
  # Only Cloudflare's edge may reach the origin; no port 80 (Origin CA cert, no ACME).
  WEB_PORTS=tcp:443
  WEB_SOURCES=$(curl -fsS https://api.cloudflare.com/client/v4/ips |
    python3 -c 'import json,sys;print(",".join(json.load(sys.stdin)["result"]["ipv4_cidrs"]))')
else
  WEB_PORTS=tcp:80,tcp:443
  WEB_SOURCES=0.0.0.0/0
fi
if exists g compute firewall-rules describe garmin-mcp-allow-web; then
  g compute firewall-rules update garmin-mcp-allow-web --allow="$WEB_PORTS" --source-ranges="$WEB_SOURCES"
else
  g compute firewall-rules create garmin-mcp-allow-web --network=default --direction=INGRESS \
    --allow="$WEB_PORTS" --source-ranges="$WEB_SOURCES" --target-tags="$WEB_TAG"
fi
exists g compute firewall-rules describe garmin-mcp-allow-iap-ssh ||
  g compute firewall-rules create garmin-mcp-allow-iap-ssh --network=default --direction=INGRESS \
    --allow=tcp:22 --source-ranges=35.235.240.0/20 --target-tags="$SSH_TAG"

if exists g compute firewall-rules describe default-allow-ssh; then
  echo
  echo "!! The default network has 'default-allow-ssh' (tcp:22 from 0.0.0.0/0, all VMs)."
  echo "   With it in place SSH is NOT restricted to IAP. It applies to every VM in this"
  echo "   project's default network, so only delete it if nothing else relies on it."
  ans="${DELETE_DEFAULT_SSH:-}"
  [[ -z "$ans" && -t 0 ]] && read -r -p "   Delete default-allow-ssh now? [y/N] " ans
  if [[ "$ans" =~ ^[Yy] ]]; then g compute firewall-rules delete default-allow-ssh --quiet; fi
fi

echo "==> VM"
exists g compute instances describe "$VM_NAME" --zone "$ZONE" ||
  g compute instances create "$VM_NAME" --zone "$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family="$IMAGE_FAMILY" --image-project=debian-cloud \
    --boot-disk-size="${DISK_SIZE:-30GB}" --boot-disk-type="${DISK_TYPE:-pd-standard}" \
    --address="$IP" \
    --service-account="$SA" --scopes=cloud-platform \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
    --tags="$WEB_TAG,$SSH_TAG"

cat <<EOF

Done. Static IP: $IP

Next:
  1. DNS: A record  $DOMAIN -> $IP   (IPv4; wait for it to resolve before step 3)
  2. GitHub OAuth App (github.com/settings/developers):
       Homepage URL:  https://$DOMAIN
       Callback URL:  https://$DOMAIN/github/callback
     then store its client secret:
       read -rs S; printf '%s' "\$S" | gcloud secrets versions add $GH_SECRET --project $PROJECT --data-file=-
  3. ./deploy.sh --bootstrap
  4. SSH in and run the Garmin login (see README).

Your account needs roles/iap.tunnelResourceAccessor and OS Login
(roles/compute.osAdminLogin) on the project to SSH via IAP.
EOF
