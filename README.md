# garmin-mcp

A remote MCP server that gives Claude (web, desktop, mobile) read access to your
Garmin Connect data. It uses streamable HTTP with MCP OAuth 2.1, and GitHub handles login.

> **Heads-up:** Garmin has no personal API. This uses the unofficial
> [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library,
> which talks to Garmin's private endpoints. That is against Garmin's terms of
> use and can break without notice. It's fine for personal use, but expect occasional breakage.

## Tools

| Tool | What it does |
|---|---|
| `recent_activities(days=7, sport=None)` | Returns a compact list of recent activities: date, sport, duration, distance, HR, pace or speed, power, swim pool length and strokes, training effect, and ID. `sport` accepts `run`, `bike`, `swim`, `brick`, `strength`, `walk` or `hike`. |
| `search(query)` | Finds `garminconnect` methods (name, signature, summary, read/write). The index is built by introspecting the installed library at startup. |
| `execute(method, args, full=false)` | Calls one allowlisted method. Reads are allowed. Writes are blocked unless `GARMIN_ALLOW_WRITES=true`. Credential, raw-request, file-path, GraphQL and binary methods are always blocked. Bulky time series are dropped unless `full=true`, and every response is size-capped with a note when it's truncated. |

## Layout

```
src/garmin_mcp/
  config.py            env settings (PUBLIC_BASE_URL -> issuer, /mcp, /github/callback)
  tokens.py            TokenStore + EncryptedFileTokenStore (Fernet, 0700/0600, atomic)
  providers/base.py    GarminProvider interface + caller-safe errors
  providers/unofficial.py  garminconnect implementation (backoff, token write-back, Cloudflare detection)
  providers/official.py    stub for Garmin's Connect Developer Program
  registry.py          introspected method index + allowlist
  shaping.py           bulky-field removal + size cap
  resilience.py        TTL cache + per-user rate limiter
  tools.py             search / execute / recent_activities (transport-independent)
  poller.py            workout-sync poller: new activities -> Claude Code routine
  auth.py              MCP OAuth authorization server backed by GitHub
  webauth.py           Rabbit web app: Google sign-in + cookie sessions
  webpush.py           Rabbit web app: Web Push (VAPID + aes128gcm), SSRF-guarded
  webapp.py            Rabbit web app: /app routes and static frontend
  appstore.py          small Fernet-encrypted JSON docs (sessions, per-user data)
  server.py            MCPServer app, uvicorn on 127.0.0.1
  login_cli.py         garmin-mcp-login
web/                   Rabbit frontend (Vite + React, ported from Dolly); builds into src/garmin_mcp/web_dist
brand/                 Rabbit logo (SVG)
infra/                 setup.sh, bootstrap-vm.sh, systemd unit, Caddyfile
deploy.sh              push + restart over IAP
scripts/smoke_live.py  live check against your real account (not CI)
```

## Local development

```bash
uv sync                       # Python 3.12 + pinned deps from uv.lock
uv run pytest                 # unit + OAuth end-to-end tests (Garmin and GitHub mocked)

# One-off local key (keep it out of git):
export GARMIN_MCP_FERNET_KEY=$(uv run python -c 'from garmin_mcp.tokens import generate_key; print(generate_key())')
export PUBLIC_BASE_URL=http://localhost:8000 DATA_DIR=./data

# Log in to Garmin locally (prompts for email, password and MFA). Tokens go to ./data/tokens/<user>.fernet
uv run garmin-mcp-login --user andrew

# Run the server without OAuth (refused unless PUBLIC_BASE_URL is localhost):
DEV_NO_AUTH_USER=andrew uv run garmin-mcp
```

Note that a local login comes from your home IP. Garmin rate-limits logins per IP
and fingerprint, so do the real login on the VM (below) rather than copying
tokens around.

### Testing with MCP Inspector

```bash
DEV_NO_AUTH_USER=andrew uv run garmin-mcp          # terminal 1
npx @modelcontextprotocol/inspector                # terminal 2
```
In the Inspector, choose transport **Streamable HTTP** and URL `http://localhost:8000/mcp`.

To exercise the real OAuth flow locally, create a second GitHub OAuth App with callback
`http://localhost:8000/github/callback`, then drop `DEV_NO_AUTH_USER` and set
`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` and `ALLOWED_GITHUB_USERS`.
Inspector's loopback redirect is allowed by `OAUTH_ALLOW_LOOPBACK_REDIRECTS`.

## GCP setup (one time)

Prerequisites: `gcloud` authenticated, a domain you control, and a GitHub account.

1. `cp infra/config.env.example infra/config.env` and fill it in. Get your GitHub numeric ID with
   `gh api users/<your-login> --jq .id`. The allowlist uses the numeric ID because
   usernames can be renamed and re-registered by someone else.
2. Create a **GitHub OAuth App** (Settings → Developer settings → OAuth Apps):
   - Homepage: `https://<DOMAIN>`
   - Callback: `https://<DOMAIN>/github/callback`
   - Copy the client ID into `infra/config.env`, and keep the client secret handy.
3. Run `./infra/setup.sh`. It:
   - enables the Compute, Secret Manager and IAP APIs;
   - creates the `garmin-mcp-vm` service account with **no project roles**, only `secretAccessor` on the two secrets;
   - generates the Fernet key straight into Secret Manager (it never touches disk) and stores the GitHub secret, which it prompts for;
   - reserves a static IP;
   - opens firewall rules for 80/443 to the world and 22 only from IAP (`35.235.240.0/20`);
   - creates an e2-small Shielded VM with OS Login.

   It offers to delete the default network's `default-allow-ssh` rule. Without that, SSH is not IAP-only.
4. Point an **A record** for `<DOMAIN>` at the printed IP and wait until it resolves.
5. Run `./deploy.sh --bootstrap`. This:
   - uploads the code;
   - installs Caddy, uv 0.12.21 and Python 3.12 (uv-managed, under `/opt/uv`);
   - creates the `garmin-mcp` system user and its directories;
   - writes `/etc/garmin-mcp/env` from `.env.example` using your config;
   - installs the systemd unit and Caddyfile, then starts everything.

### Behind Cloudflare

`BEHIND_CLOUDFLARE=true` in `infra/config.env`. With Cloudflare in front, the deployment looks like this:
- **DNS:** a proxied A record for `<DOMAIN>` (e.g. `garmin.example.com`) pointing at the VM's static IP.
- **TLS:** a Cloudflare Origin CA certificate (ECC, valid until 2041) in `/etc/caddy/certs/`.
  The key was generated on the VM and never left it; only the CSR was sent to Cloudflare.
  Caddy uses it automatically when the files exist (no ACME).
- **Cloudflare rules** (in the domain's zone, scoped to this host only):
  - A Configuration Rule sets SSL to **Full (strict)** and turns the browser integrity check off.
  - A custom WAF **skip** rule applies only to Anthropic's egress range (`160.79.104.0/21`), so
    bot and AI-bot protection don't block the connector's server-to-server calls.
- **GCP firewall:** only Cloudflare's IPv4 ranges can reach port 443; port 80 is closed.
  If Cloudflare changes its ranges, re-run `infra/setup.sh`.

To SSH in: `gcloud compute ssh <VM_NAME> --zone <ZONE> --tunnel-through-iap`. Your account needs
`roles/iap.tunnelResourceAccessor` and `roles/compute.osAdminLogin`.

## Garmin login on the VM (bootstrap and re-auth)

```bash
gcloud compute ssh garmin-mcp --zone <ZONE> --tunnel-through-iap
sudo garmin-mcp-login --user andrew
```

- The command prompts for your email, then your password (not echoed), then the MFA code if Garmin asks for one.
- It makes **one** login attempt. Internally `garminconnect` tries its own strategy chain once, which can take up to a minute. **Nothing retries automatically.** If Garmin returns 429 or a Cloudflare challenge, wait before trying again.
- The password exists only in the CLI process's memory and is never written anywhere. Only Garmin's OAuth tokens are stored, Fernet-encrypted, in `/var/lib/garmin-mcp/tokens/<user>.fernet`.
- The running server notices new tokens on its next request, so no restart is needed.

Then run the live smoke test:
```bash
sudo -u garmin-mcp bash -c 'set -a; . /etc/garmin-mcp/env; set +a; \
  /opt/garmin-mcp/current/.venv/bin/python /opt/garmin-mcp/current/scripts/smoke_live.py --user andrew'
```

**Re-authenticating:** when a tool says "Garmin authorization … is not usable … re-run
garmin-mcp-login", run the same `sudo garmin-mcp-login --user andrew`.

**Backups:** there are none, on purpose. If the token file or Fernet key is lost,
re-running the login CLI is the recovery path.

**If every API call returns 403 after a successful login:** Garmin's edge is rejecting the
plain-`requests` TLS fingerprint used for API calls from datacenter IPs
([python-garminconnect#444](https://github.com/cyberjunky/python-garminconnect/issues/444)).
Set `GARMIN_API_TLS_IMPERSONATION=true` in `/etc/garmin-mcp/env`, then run
`sudo systemctl restart garmin-mcp` and re-run the login.

## Deploying updates

```bash
uv lock                 # only when you change dependencies
./deploy.sh             # upload working tree, uv sync --frozen, switch release, restart, health check
./deploy.sh --rollback  # back to the previous release
```
Releases live in `/opt/garmin-mcp/releases/<timestamp-sha>`. They are owned by root and read-only to the service, and the last 3 are kept.
Logs: `journalctl -u garmin-mcp -f` (JSON lines; no tokens or health payloads).

## Adding to claude.ai as a custom connector

1. Go to claude.ai → **Settings → Connectors → Add custom connector**.
2. Enter the URL `https://<DOMAIN>/mcp`. Leave the OAuth client ID and secret empty, because Claude registers itself via DCR.
3. Click **Connect**. You'll be sent to GitHub. Approve, and you land back in Claude.
4. The connector then works on web, desktop and mobile. Try "What did I do this week?" or
   "Show HRV and sleep for the last 5 nights vs my long run on Sunday".

Access tokens last 1 hour and refresh tokens last 30 days, rotated on every use. Removing your ID from
`ALLOWED_GITHUB_USERS` (then restarting) revokes access immediately.

## Adding more users later

1. Add `<github_id>:<user_id>` to `ALLOWED_GITHUB_USERS` in `/etc/garmin-mcp/env` and restart.
2. `sudo garmin-mcp-login --user <user_id>` with their Garmin credentials, over SSH, with them present for MFA.

Each user's tokens, cache entries and rate-limit bucket are keyed by `user_id`.

## Workout-sync poller (optional)

A background thread in the server checks Garmin for new activities and fires a
[Claude Code routine](https://claude.ai/code/routines) through its API trigger, once per batch,
with one line per activity (`id | sport | start time local | duration | distance`). It runs every 5 min
from 5 to 9:55am and hourly otherwise, in `America/Denver` (`POLL_*` in `.env.example`).
Each poll logs one `poll` line with `checked`, `new` and `triggered`.

- **First run** only records the newest activity ID; it doesn't fire.
- **At most once per activity.** The last-seen ID advances only after the routine returns 200, so
  failures retry on the next poll. If the outcome is unknown (a timeout after sending, or a crash
  mid-fire), it advances anyway and logs `routine_fire_outcome_unknown`, because `/fire` has no
  idempotency key and retrying could start a duplicate review.
- **Self-healing.** Errors never stop the loop. Garmin or routine failures back off from 5 min
  up to 1 h, a routine 429 waits for `Retry-After`, and a Garmin re-auth error pauses polling until
  `garmin-mcp-login` writes new tokens. A file lock allows one poller per data dir, and
  `POLL_MAX_FIRES_PER_DAY` caps fires as a circuit breaker.
- **Multisport:** child activities (with a `parentId`) are skipped and the parent is reported.

State: `/var/lib/garmin-mcp/poller/state.json`. Delete it to re-baseline.

To enable it:
1. Create the routine at claude.ai/code/routines, add an API trigger, and copy its ID and token.
2. Store the token, piped straight from the prompt into Secret Manager:
   ```bash
   gcloud secrets create garmin-mcp-routine-token --project <PROJECT> --replication-policy=automatic
   read -rs T; printf '%s' "$T" | gcloud secrets versions add garmin-mcp-routine-token --project <PROJECT> --data-file=-
   gcloud secrets add-iam-policy-binding garmin-mcp-routine-token --project <PROJECT> \
     --member=serviceAccount:garmin-mcp-vm@<PROJECT>.iam.gserviceaccount.com \
     --role=roles/secretmanager.secretAccessor --condition=None
   ```
3. Add `POLL_USER_ID=andrew`, `ROUTINE_ID=<id>`, `ROUTINE_TOKEN_SECRET_NAME=garmin-mcp-routine-token`
   and `POLL_DRY_RUN=true` to `/etc/garmin-mcp/env`, run `sudo systemctl restart garmin-mcp`, and watch
   `journalctl -u garmin-mcp -f | grep poll`. A dry run logs the text it would send and saves nothing.
4. When the output looks right, set `POLL_DRY_RUN=false` and restart.

## Rabbit web app (optional)

Rabbit is a phone-friendly coach app at `$PUBLIC_BASE_URL/app/` for people who won't set up an MCP
connector: Google sign-in, Web Push, a Connect Garmin page, and a coach agent (Claude Haiku) with
morning briefs, workout notes, a plan view and threads. The
plan is in `docs/plans/web-app.md`.

**It runs as a separate service** so testing it can never take down the MCP connector:
`rabbit.service` on 127.0.0.1:8001, its own system user (`rabbit`), data dir (`/var/lib/rabbit`),
release dir (`/opt/rabbit`), env file (`/etc/rabbit/env`) and Fernet key. Caddy sends `/app` and
`/app/*` to it and everything else to the MCP server; if Rabbit is down, only `/app` returns 502.
The same codebase runs in both, with `MCP_ENABLED=false` in Rabbit's env (no `/mcp`, no GitHub
OAuth). Garmin tokens for Rabbit users live only in Rabbit's data dir, so each user's tokens are
still refreshed by exactly one process.

### Rabbit local dev

`scripts/dev-rabbit.sh` runs Rabbit on its own (`MCP_ENABLED=false`, port 8001), never touching
the MCP server. Local keys, sessions and push subscriptions live in `./data/rabbit-dev`.

- **Mac:** `scripts/dev-rabbit.sh mac`, then open http://localhost:5173/app/ (Vite hot reload).
  Google is skipped (`DEV_NO_AUTH_USER=andrew`, localhost only). Chrome allows notifications on
  localhost, so push works on the Mac too.
- **Phone:** iOS only allows service workers, Web Push and secure cookies over real HTTPS, and
  Google rejects LAN IPs and `.local` redirect URIs. So phone mode exposes the Mac through a
  Cloudflare Tunnel on a fixed subdomain, with real Google sign-in (the dev bypass is refused for
  non-localhost URLs). One-time setup:
  ```bash
  brew install cloudflared
  cloudflared tunnel login                       # browser: pick the anlinguist.com zone
  cloudflared tunnel create rabbit-dev
  cloudflared tunnel route dns rabbit-dev rabbit-dev.anlinguist.com
  ```
  Add `https://rabbit-dev.anlinguist.com/app/auth/google/callback` as a redirect URI on the Google
  OAuth client, then create `data/rabbit-dev/phone.env` (mode 600):
  ```bash
  RABBIT_DEV_URL=https://rabbit-dev.anlinguist.com
  CLOUDFLARE_TUNNEL=rabbit-dev
  GOOGLE_CLIENT_ID=...apps.googleusercontent.com
  GOOGLE_CLIENT_SECRET=...
  ALLOWED_GOOGLE_USERS=you@gmail.com:andrew
  ```
  Run `scripts/dev-rabbit.sh phone`. The frontend rebuilds on save; refresh the phone.

On the VM:
1. `./infra/setup.sh` creates `garmin-mcp-google-client-secret` and `garmin-mcp-anthropic-api-key`
   (empty), `garmin-mcp-vapid-private-key` and `garmin-mcp-rabbit-fernet-key` (generated) and grants
   the VM access. Store the Claude API key (from the Console; set a spend limit there too):
   ```bash
   pbpaste | tr -d '\n' | gcloud secrets versions add garmin-mcp-anthropic-api-key --project <PROJECT> --data-file=-
   ```
2. Google Cloud console → **APIs & Services → OAuth consent screen**: External, scopes `openid` and
   `email` only, then **Publish app** (no verification needed for these scopes). **Credentials →
   Create OAuth client ID → Web application**, authorized redirect URI
   `https://<domain>/app/auth/google/callback`. Store the secret:
   ```bash
   read -rs S; printf '%s' "$S" | gcloud secrets versions add garmin-mcp-google-client-secret --project <PROJECT> --data-file=-
   ```
3. `./deploy.sh --rabbit --bootstrap`: creates the user, dirs, `/etc/rabbit/env` (from
   `infra/rabbit.env.example`) and the unit, and re-renders the Caddyfile (validated first; a
   graceful reload that doesn't restart the MCP server). The first start fails until step 4.
4. On the VM, fill in `GOOGLE_CLIENT_ID`, `ALLOWED_GOOGLE_USERS` and `VAPID_SUBJECT` in
   `/etc/rabbit/env`, then `sudo systemctl restart rabbit`.
5. Later updates: `./deploy.sh --rabbit` (builds `web/` locally with bun; restarts only Rabbit).
   `./deploy.sh --rabbit --rollback` rolls back only Rabbit. Logs: `journalctl -u rabbit -f`.

On an iPhone: open the URL in Safari → Share → **Add to Home Screen**, open Rabbit from the Home
Screen, sign in, tap **Turn on notifications**, then **Send test**, and run the streaming check.
Removing someone from `ALLOWED_GOOGLE_USERS` (and restarting) revokes their sessions immediately.

## Switching to Garmin's official API later

Implement `OfficialApiProvider` (`providers/official.py`) against the Connect Developer
Program's OAuth and its push-based Health/Activity APIs, then swap it in `server.create_app`.
The tools depend only on `GarminProvider`, `GarminSession` and the method registry.

## Configuration

Every variable is listed and explained in [`.env.example`](.env.example).
