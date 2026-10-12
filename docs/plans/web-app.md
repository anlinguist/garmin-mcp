# Plan: Rabbit, a Garmin training coach web app (multi-user)

"Rabbit" is the pacer in a race: it sets the pace so you can hit your goal.
Sibling to Dolly (`web-mcp`): same look, own logo.

Status: Phase 1 built on branch `rabbit-web` (2026-10-11): config, encrypted store, Google sign-in + sessions, Web Push, `/app` routes with the Phase 0 device checks, and the Vite frontend scaffold with Dolly's styles and the Rabbit mark. Not yet deployed.

## Goal

Give a non-technical friend the same Garmin-backed training agent Andrew has in
claude.ai, without requiring a paid AI subscription, a GitHub account, or any
terminal work. The friend's whole setup:

1. Open a link on his iPhone, tap **Share → Add to Home Screen**.
2. Open the icon, **Sign in with Google**.
3. **Connect Garmin** (email, password, MFA code if asked).
4. Tap **Turn on notifications**.

After that he chats with the agent in the app and gets pushed messages (new
workout feedback, a morning brief). Andrew pays the Claude API costs.

## Non-goals (v1)

- Native iOS app, TestFlight, APNs.
- SMS, iMessage, or RCS.
- Open sign-up. The app is invite-only (allowlist).
- Garmin writes. The agent is read-only regardless of `GARMIN_ALLOW_WRITES`.
- Generating a full training plan. The agent keeps notes about the user's plan
  and goals; plan authoring is a later feature.

## Architecture

Rabbit runs as its **own service** on the same VM (`rabbit.service`, 127.0.0.1:8001,
own system user, data dir, release dir, env file and Fernet key), from the same
codebase with `MCP_ENABLED=false`. Caddy routes `/app*` to it and everything else
to the MCP server, so testing, deploying or crashing Rabbit never interrupts the
claude.ai connector (changed 2026-10-11 at Andrew's request; originally one process).

Resources (e2-micro, 1 GB RAM + 1 GB swap, shared vCPU): measured idle RSS is ~75 MB
per process; the Anthropic SDK adds ~56 MB, so Rabbit is ~130 MB plus a few MB per
in-flight turn (Garmin JSON, conversation). The agent is I/O-bound (waiting on the
Claude API and Garmin), with at most 2 concurrent turns. `rabbit.service` sets
`MemoryHigh=300M`, `MemoryMax=400M`, `OOMScoreAdjust=500` and `CPUWeight=50` so that
under pressure Rabbit is throttled or killed, never the MCP server. Upgrade path if
it's ever tight: e2-small (2 GB), which leaves the free tier (check current pricing).

Garmin rotates the refresh token on every refresh, so a given user's tokens must
be owned by exactly one process. Rabbit users' tokens live only in Rabbit's data
dir; Andrew's MCP tokens live only in the MCP server's. If Andrew also uses
Rabbit, he connects Garmin separately inside Rabbit (its own login, its own
token chain).

```
iPhone (installed web app)
   │  HTTPS, same origin: <domain>/app/...
   ▼
Caddy ─┬─► garmin-mcp.service :8000   /mcp, /authorize, /token, /github/callback (unchanged)
       └─► rabbit.service     :8001   (/app*, MCP_ENABLED=false)
          ├─ /app/*            static PWA shell (HTML/JS/CSS, manifest, service worker)
          ├─ /app/auth/*       Google sign-in → session cookie
          ├─ /app/api/*        chat (NDJSON stream), history, settings, Connect Garmin, push subscribe
          ├─ agent.Agent       Claude API tool loop over GarminService (read-only)
          ├─ poller thread     multi-user: new activity → agent turn → store → web push
          └─ GarminService → Registry → UnofficialProvider (existing, unchanged)
```

The MCP server for claude.ai keeps working exactly as today. Andrew can keep
using claude.ai + the routine, use the web app, or both.

## Decisions (with reasons)

| Area | Decision | Why |
|---|---|---|
| Surface | Installable web app (PWA) with Web Push | No App Store, no $99/yr, no 90-day TestFlight expiry; deploys with `./deploy.sh`. iOS supports Web Push for Home Screen web apps since 16.4. |
| Frontend | Vite + React 19 SPA, ported from Dolly's chat UI (`web-mcp`, MIT) | Dolly's chat is already a phone-width single-column UI (built for the extension side panel) with streaming markdown, tool cards and bottom sheets. Every chat file is `'use client'` with no `next/*` imports, so it runs in a plain Vite SPA. Built locally by `deploy.sh`; the VM never needs Node. See "Frontend" below. |
| Identity | Google OAuth (authorization code + PKCE), server-side, `openid email` scopes | Mirrors the GitHub flow in `auth.py`: exchange code, read userinfo once, discard Google tokens. No JWT library needed. Only non-sensitive scopes, so the consent screen can be published without Google app verification. |
| Allowlist | `ALLOWED_GOOGLE_USERS=<email>:<user_id>`; Google `sub` is bound on first sign-in and required to match afterwards | Andrew doesn't know the friend's `sub` in advance. Binding on first login gets the same stability guarantee as numeric GitHub IDs. Requires `email_verified=true`. |
| Sessions | Opaque random token in a `__Host-` cookie (Secure, HttpOnly, SameSite=Lax, Path=/), SHA-256 hash stored server-side, 90-day sliding expiry | Survives restarts (hashes persist encrypted, like refresh tokens today). Lax rather than Strict so the session is sent on the redirect back from Google. |
| Agent harness | Hand-written tool loop on the `anthropic` Python SDK (1.x) | The SDK is built on `httpx2`, which matches the project; its constraints (`httpx2>=2,<3`, `anyio>=4.1,<5`, `pydantic<3`) fit the current lock. A manual loop (rather than the beta tool runner) makes it easy to stream progress to the browser and enforce per-user budgets. The Claude Agent SDK and Vercel AI SDK were ruled out earlier (too heavy / wrong language and process). |
| Model | `claude-haiku-5-5` for chat (effort `low`/`medium`); optionally `claude-sonnet-5-5` for the morning brief | Haiku 5.5 is $0.10 / $0.50 per MTok input/output. A typical chat turn with two or three tool calls is roughly 20-40k input tokens, i.e. well under a cent. Sonnet 5.5 ($2 / $10) only where quality matters more. Both configurable. |
| Push | Web Push with VAPID, implemented on `py-vapid` + `http-ece` + `httpx2` | `pywebpush` pulls in both `requests` and `aiohttp`; the sending code is small enough to own, and keeps one HTTP client. |
| Storage | Fernet-encrypted JSON files under `DATA_DIR/app/` | Same pattern as tokens and OAuth state. Two or three users don't need a database. |
| Garmin login | Web "Connect Garmin" page calling the existing `login_start` / `login_complete`; CLI stays as admin fallback | Same single-attempt, never-automatic rules as the CLI, enforced by locks and cooldowns instead of operator discipline. |

## Data layout

All files Fernet-encrypted with the existing key, written atomically (temp file
+ fsync + rename), dirs 0700, files 0600.

```
DATA_DIR/
  tokens/<user_id>.fernet              existing Garmin tokens
  oauth/...                            existing MCP OAuth state
  poller/<user_id>.json                poll cursor per user (generalized from poller/state.json)
  app/
    identities.fernet                  {google_sub: user_id} bindings
    sessions.fernet                    {sha256(token): {user_id, created, last_seen}}
    users/<user_id>/
      profile.fernet                   timezone, brief time, notes (agent memory), model override
      conversation.fernet              completed turns: model context (text only) + display parts (see Agent)
      push.fernet                      Web Push subscriptions
      usage.fernet                     daily token counts for the budget
```

## Server work

### 1. Config (`config.py`, `.env.example`)

New settings, all optional so the app is off unless configured:

- `APP_ENABLED` (default false)
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET_NAME` (Secret Manager; env fallback `GOOGLE_CLIENT_SECRET`)
- `ALLOWED_GOOGLE_USERS=<email>:<user_id>,...`
- `ANTHROPIC_API_KEY_SECRET_NAME` (env fallback `ANTHROPIC_API_KEY`)
- `AGENT_MODEL=claude-haiku-5-5`, `AGENT_BRIEF_MODEL` (defaults to `AGENT_MODEL`)
- `AGENT_DAILY_TOKEN_BUDGET` per user (default e.g. 2M tokens)
- `VAPID_PRIVATE_KEY_SECRET_NAME`, `VAPID_SUBJECT=mailto:...`
- `POLL_USERS=andrew:routine,friend:app` replaces `POLL_USER_ID` (keep `POLL_USER_ID` as an alias for `<id>:routine`)

Validation follows the existing style: fail at startup with a clear message on
malformed entries.

### 2. Encrypted JSON store (`appstore.py`)

Generalize `EncryptedFileTokenStore` into a small `EncryptedJSON` helper (load,
save, update under a per-file lock). Reuse it for every file in `app/`.

### 3. Google sign-in and sessions (`webauth.py`)

- `GET /app/auth/google/start`: random `state`, PKCE verifier, and `nonce`, kept
  in memory for 10 minutes; redirect to Google.
- `GET /app/auth/google/callback`: check `state`, exchange the code at Google's
  token endpoint, call the OIDC userinfo endpoint, require `email_verified`,
  map email → user_id via the allowlist, bind or verify `sub`, then discard the
  Google tokens. Issue the session cookie and redirect to `/app/`.
- `POST /app/auth/logout`: revoke the session.
- `require_user(request)`: resolve the cookie → session → user_id, and re-check
  the allowlist on every request (same as `server.current_user()`).
- CSRF: every mutating `/app/api/*` request must carry `Origin` equal to
  `PUBLIC_BASE_URL` and `Content-Type: application/json`.

### 4. Connect Garmin (`garmin_connect_web.py`)

- `POST /app/api/garmin/login {email, password}` → `{job_id}`. Runs
  `provider.login_start` once on a worker thread.
- `GET /app/api/garmin/login/{job_id}` → `pending | needs_mfa | ok | error | cooldown`.
  The page polls every 2s; a login can take up to a minute, too long to hold a
  request open behind Cloudflare.
- `POST /app/api/garmin/mfa {job_id, code}` → `provider.login_complete`, up to 3
  tries on the same handle (matching the CLI). Handles expire after 10 minutes.
- `POST /app/api/garmin/disconnect` → delete tokens, invalidate the session,
  clear the cache.

Guardrails:

- One login in flight server-wide (global lock). Garmin rate-limits per IP and
  every user shares the VM's IP.
- Escalating per-user cooldown, by failure type (decided 2026-10-11):
  - Wrong email or password (Garmin rejected the credentials): 1 min, then 5, 15, 60 min.
    The ladder resets on success or after 24 h with no failures.
  - Garmin 429 / `CloudflareBlocked`: a **global** cooldown (shared IP), 60 min doubling to
    a 24 h cap, honoring `Retry-After` if Garmin sends one. Reports show 429s that look
    account-level and last 48 h+ after repeated attempts, so the UI says "Garmin is
    rate-limiting logins; try again after <time>" and offers no retry button.
  - Wrong MFA code doesn't start a new login: up to 3 tries on the same handle.
- Dev mode: `RABBIT_FAKE_GARMIN=1` (localhost/dev only) swaps in a fake provider that
  scripts success, MFA, wrong password and 429, so the UI and cooldowns can be exercised
  without real Garmin logins. Real logins happen once, deliberately.
- The password only ever appears in the POST body. It is held in memory for the
  single attempt, then dropped, and never logged or echoed in errors (reuse
  `logs.redact`).
- Jobs are keyed to the session's user_id; another user can't poll or complete
  them.

### 5. Agent (`agent.py`)

- Tools (JSON schemas, `strict: true`): `search`, `execute`, `recent_activities`
  backed by `GarminService` with writes forced off, plus `update_notes` (replace
  the user's notes, capped at about 2 KB). `GarminError` becomes a
  `tool_result` with `is_error: true` and the safe message; any other exception
  becomes a generic error.
- System prompt: the coaching persona plus the server `INSTRUCTIONS` text,
  frozen and cached (`cache_control`). The user's notes and the current local
  date/time go in the first user message of each turn, not the system prompt,
  so the cached prefix stays stable.
- History: each completed turn is stored twice. The **model context** keeps
  only text (user message plus final assistant text); tool calls and thinking
  blocks live only inside the turn, which stays append-only. This keeps context
  small and avoids the history-editing rules for replayed thinking blocks on
  current models. The **display parts** use Dolly's ordered parts format
  (`{type:'text'}` / `{type:'tool_call', toolCallId, toolName, params, result?}`)
  so tool cards re-render on reload. Display results are stored compacted
  (capped size); the UI doesn't need full Garmin payloads. Keep the last ~30
  turns of model context and ~200 of display.
- Limits: at most 8 tool iterations per turn; tool results capped by the
  existing `max_response_bytes`; one turn at a time per user; at most 2
  concurrent turns server-wide (e2-micro); per-user daily token budget with a
  friendly "back tomorrow" message.
- Handle `stop_reason` `refusal` and `max_tokens` with a user-facing message.
  Haiku 5.5 has no server-side refusal fallback.
- Logging: event, user_id, model, token counts, tool names, latency. Never
  message text, notes, or tool payloads.

### 5a. User isolation (direct calls, no MCP hop)

The agent calls `GarminService` in-process instead of going through the MCP
server. Identity has to stay bound the same way MCP binds it today:

- `user_id` comes only from the server-side session (or, for proactive turns,
  from the poller's own per-user loop). It is never taken from a request body,
  a query parameter, or tool arguments.
- Tools are built per turn as closures over that `user_id`. No tool schema has
  a user field, so the model (or a prompt injection in Garmin data) has nothing
  to change.
- No module-level or thread-local "current user". Everything takes `user_id`
  explicitly, as `GarminService` already does.
- Conversation, notes, push subscriptions, usage, poll state and Garmin tokens
  all live under per-user paths derived from the validated `user_id`
  (`validate_user_id`), so a path can't point at another user's files.
- Login jobs, chat streams and in-flight turns are keyed by session `user_id`
  and checked on every follow-up request.
- The three tool definitions (names, descriptions, parameters) live in one
  module that both `server.py` (MCP) and `agent.py` read, so the two front
  doors can't drift.

Tests: two users with separate `FakeGarmin` instances. Assert that a turn,
a poll, a brief and a push for user A never touch B's session, files or
subscriptions. Assert that tool schemas contain no user field. Assert that
history and login-job endpoints return 404 across users.

### 6. Chat API

- `POST /app/api/chat {message}` → `application/x-ndjson`, one JSON event per
  line, using the subset of Dolly's `StreamEvent` protocol so its client
  (`agent-stream.ts`) ports unchanged:
  ```
  {type:'text-delta', text}
  {type:'tool-call-start', toolCallId, toolName, params}
  {type:'tool-call-result', toolCallId, toolName, result}   // compacted result
  {type:'tool-call-error', toolCallId, toolName, error}     // GarminError message only
  {type:'usage', promptTokens, completionTokens, totalTokens}
  {type:'result', response: {status:'done', message, steps} | {status:'error', error, steps}}
  ```
  Plus blank-line heartbeats, matching Dolly's inactivity timeout. Not used:
  `approval-request` (tools are read-only), `form-request`, `ui-delta` /
  `ui-component` (json-render; a v2 candidate for workout charts),
  `balance-update`. The loop runs on a worker thread and feeds the
  `StreamingResponse` through an anyio memory channel. Messages are capped at
  4,000 characters.
- Single thread per user, not Dolly's conversation list: proactive messages
  (workout feedback, briefs) need one place to land, and a coach chat reads
  better as one ongoing thread.
- `GET /app/api/messages?before=<id>` → paged history in the parts format,
  including proactive messages.
- `GET /app/api/me` → user_id, Garmin connected?, push enabled?, settings.
- `PUT /app/api/settings {timezone, brief_time}`.

Caddy already sets `flush_interval -1` and has no `encode`, so the NDJSON stream flows
through unchanged.

### 7. Web Push (`webpush.py`)

- `POST /app/api/push/subscribe {endpoint, keys}`, `DELETE` to remove it.
- SSRF guard: accept only `https` endpoints whose host is a known push service
  (`web.push.apple.com`, `fcm.googleapis.com`,
  `updates.push.services.mozilla.com`, `*.notify.windows.com`). Phase 0 confirms
  the Apple host.
- Send: VAPID JWT (`py-vapid`), aes128gcm payload (`http-ece`), POST via
  `httpx2`. A 404 or 410 deletes the subscription. The payload carries only a
  short title, body, and `/app/` URL. The full message is fetched in-app.

### 8. Multi-user poller and briefs (`poller.py`)

- `POLL_USERS` creates one `Poller` per user, polled sequentially by the one
  existing thread on the existing schedule, each with its own state file and
  lock. The at-most-once delivery invariant is unchanged.
- The delivery target is pluggable: `routine` (today's `RoutineClient`) or
  `app`, which runs an agent turn ("New activity: <activity line>. Give brief
  feedback."), appends the result to the conversation as an assistant message,
  and pushes it. "Unknown outcome" for the app target means the agent call
  failed after starting. Advance the cursor and log, matching the routine
  semantics.
- Morning brief: per-user `brief_time` in the user's timezone. Once per local
  date (stored in state), run a brief turn on `AGENT_BRIEF_MODEL` and push it.
- `ReauthRequired` for an app user sends one push ("Garmin disconnected: tap to
  reconnect") per token fingerprint, using the existing reauth backoff.

### 9. Wiring (`server.py`)

`build_app` mounts the `/app` routes when `APP_ENABLED`. They are custom routes
on the same Starlette app. MCP's DNS-rebinding settings cover `/mcp` only, so
`/app` routes get an explicit Host check. The lifespan hook starts the
agent worker pool alongside the poller.

## Frontend (`web/`, ported from Dolly)

A Vite + React 19 + Tailwind v4 SPA in a top-level `web/` directory, built to
`src/garmin_mcp/web_dist/` and served from `/app/` by Starlette `StaticFiles`.

**Port from `web-mcp` (Dolly), nearly as-is:**

- From `src/components/chat/Chat.tsx`: `MessageBubble` (react-markdown +
  remark-gfm in `prose`), `ToolCallCard`, `ToolCallGroup`, the composer
  (auto-growing textarea, Enter to send, stop button), the thinking-dots
  indicator, the auto-scroll behaviour, `formatToolName`.
- `src/lib/agent-stream.ts` (NDJSON reader with inactivity timeout), with the
  Supabase bearer token removed (the session cookie is sent automatically).
- The `StreamEvent` type subset listed under Chat API.
- `src/components/ui/*` shadcn primitives that the ported pieces use, plus
  `cn` from `src/lib/utils.ts`.
- `src/styles/globals.css` tokens, dark mode (`prefers-color-scheme`), and the
  `.modal` bottom-sheet and animation CSS. Self-host the DM Serif Display / DM
  Mono fonts so the CSP needs no Google Fonts origins.

**Rewrite:**

- `useAgent.ts` (1,112 lines, tied to the extension, Supabase, configs, MCP
  connections, balance) becomes a ~250-line `useCoach` hook: keep the
  `processStream` switch and history loading, drop client-side tools, HITL,
  queueing across conversations, and model/balance state.
- Header and branding; a single thread instead of `ConversationList`.
- `ToolCallCard` gets a coach-friendly mode: a friendly label per Garmin
  method ("Checked your sleep", "Looked at Tuesday's ride") and a collapsed
  card by default. Raw params and results stay behind the expand toggle, and
  the "Native Request" block is dropped.

**Drop:** approvals sheet, `PermissionCard`, `FormPanel`, json-render
`UIComponentCard` (v2 candidate for charts), `ModelPicker`, `UsageMeter`, file
attachments, the message queue.

**New screens** (styled with the same tokens and components):

1. **Install prompt**: shown in a regular Safari tab (not standalone), with a
   three-step Add to Home Screen illustration. Push only works from the
   installed app, so sign-in happens there.
2. **Sign in**: one Google button.
3. **Connect Garmin**: email, password, MFA code step, clear status and
   cooldown messages, a "your password is used once and never stored" note.
4. **Chat**: the ported Dolly chat. A banner offers **Turn on notifications**
   (the button calls `Notification.requestPermission()`, since iOS requires a
   tap) and **Reconnect Garmin** when needed.
5. **Settings** (bottom sheet): timezone, brief time, disconnect Garmin, sign
   out.

**PWA bits:** `manifest.webmanifest` (`display: standalone`, `scope: /app/`,
`start_url: /app/`, icons), `sw.js` (push and notificationclick handlers only;
no offline caching in v1), `100dvh` instead of `h-screen`, and
`env(safe-area-inset-*)` padding for the notch and home indicator.

**Dependencies** (exact pins in `web/package.json`, `bun.lock` committed):
react, react-dom, react-markdown, remark-gfm, tailwindcss + `@tailwindcss/vite`
+ `@tailwindcss/typography`, radix-ui, clsx, tailwind-merge,
class-variance-authority, lucide-react, vite. Run `bun audit`/`npm audit`
alongside `pip-audit`.

**CSP for `/app/`** (set by `webapp.py` on static responses): `default-src 'self'; script-src 'self';
style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self';
connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'
https://accounts.google.com`. React and Radix set inline `style` attributes, so
`style-src` needs `'unsafe-inline'`; scripts stay strict. Phase 1 verifies the
built bundle has no inline scripts.

## Infra and ops

- Google Cloud console (your project): OAuth client (Web), redirect URI
  `https://<domain>/app/auth/google/callback`, consent screen
  External and published, scopes `openid email` only.
- Secret Manager: Google client secret, Anthropic API key, VAPID private key.
  Grant the VM service account access (extend `infra/setup.sh`).
- CSP: set by the app itself on `/app` static files (one source, covered by tests), not in Caddy. Caddyfile unchanged. Still no access
  log (the callback query carries the OAuth code).
- Anthropic Console: a dedicated API key for this app, with a monthly spend
  limit on the workspace.
- `deploy.sh`: run `bun install --frozen-lockfile && bun run build` in `web/`
  before bundling, and add the built `src/garmin_mcp/web_dist/` to the tarball
  explicitly. It's gitignored, and the bundle is built from `git ls-files
  --exclude-standard`, so it would otherwise be left out. Fail the deploy if
  the build fails. The VM never runs Node.
- README: setup steps, inviting a user, rotating keys. CLAUDE.md: update the
  login rule to "login runs from the CLI or the authenticated Connect Garmin
  page, single attempt, never automatic", and describe the new modules.

## Phases

**Phase 0 results (2026-10-11, iPhone, via `scripts/dev-rabbit.sh phone` + Cloudflare Tunnel):
passed.** Google sign-in from the installed Home Screen app returned to the app signed in (no
pairing-code fallback needed). Web Push delivered; Apple's endpoint host is `web.push.apple.com`
(201), Chrome's is `fcm.googleapis.com`. NDJSON streamed incrementally through Cloudflare.

**Phase 0: spikes on a real iPhone (half a day; do first, since they can sink the design).**

- Install a stub PWA from the VM. Confirm the Google sign-in redirect round-trip
  returns into the standalone app with the session cookie set, not into Safari.
  iOS has historically been inconsistent here.
  - Fallback if it fails: a one-time pairing code. Andrew runs
    `garmin-mcp-admin invite friend`, the friend types the 6-digit code into
    the installed app, and the server issues the session. This is robust
    because no cross-app redirect is involved.
- Stream a stub NDJSON response through Cloudflare → Caddy → uvicorn and
  confirm lines arrive incrementally, not buffered until the end.
- Send a test Web Push to the installed app from the VM. Record the endpoint
  host for the SSRF allowlist.

**Phase 1: foundations.** Config, `EncryptedJSON`, sessions, Google sign-in (or
pairing code), Vite scaffold with Dolly's tokens/primitives ported, `deploy.sh`
build step, static serving, `/app/api/me`, Caddy CSP. Tests for the OAuth flow
with Google mocked, session expiry and revocation, allowlist and `sub` binding,
CSRF origin checks.

**Phase 2: Connect Garmin.** Endpoints, job model, locks and cooldowns, UI.
Tests with `FakeGarmin`: MFA path, three-tries limit, cooldowns, global lock,
cross-user job access denied, password never appears in logs (capture and
assert).

**Phase 3: agent and chat.** Agent loop, tools, budget, NDJSON stream,
history in parts format, ported Dolly chat UI + `useCoach`.
Tests: fake model client driving tool calls; one SDK-level test with a mocked
transport; registry still blocks writes and dangerous methods via the agent;
budget exhaustion; refusal handling.

**Phase 4: push, poller, briefs.** Web Push sender and subscription API,
`POLL_USERS`, app delivery target, morning brief, reauth push. Tests extend
`test_poller.py` with the existing invariants (cursor, pending_through, fire cap)
for the app target.

**Phase 5: ship.** Infra changes, README/CLAUDE.md/.env.example, `pip-audit`,
deploy, onboard the friend, security review write-up.

Rough size: 1,500-2,000 lines of Python, about 1,000 lines of ported TSX/CSS
from Dolly plus ~800 new (hook, screens, PWA bits),
plus tests.

## Security review (planned posture)

New attack surface and the control for each:

| Surface | Risk | Control |
|---|---|---|
| Google sign-in | Account confusion, login CSRF | State + PKCE + nonce; email allowlist + `email_verified`; `sub` pinned after first login |
| Session cookie | Theft, CSRF | `__Host-`, HttpOnly, Secure, SameSite=Lax; Origin + JSON content-type check on writes; server-side revocation; allowlist re-checked per request |
| Connect Garmin | Password exposure; burning the VM IP's Garmin login reputation | Body-only, memory-only, never logged; allowlisted users only; global single-flight; per-user and global cooldowns; no automatic retries |
| Push subscribe | SSRF via attacker-chosen endpoint URL | HTTPS + known push-service host allowlist; no redirects followed |
| Agent | Prompt injection via Garmin data (activity names, notes); cost runaway | Read-only tools only (writes forced off); no web or outbound tools; output goes only to the same user; iteration cap, per-user daily budget, Anthropic spend limit |
| XSS in chat | Model output rendered as HTML | react-markdown without `rehype-raw` (raw HTML in markdown is not rendered, same as Dolly); strict `script-src 'self'` CSP |
| Data at rest | VM disk compromise | Everything Fernet-encrypted with the existing Secret Manager key; no plaintext conversation logs |
| Logs | Leaking content | Event + counts only; reuse `log_event` and `redact` |

Residual risks to accept explicitly:

- The friend's Garmin password transits Andrew's server once.
- Conversation content is sent to the Anthropic API.
- Andrew, as server operator, can technically decrypt the friend's data.

## Known iOS limitations (accepted for now)

- **Keyboard settle:** when a text field is focused, iOS pans the page to keep it above the
  keyboard and the visual-viewport frame follows a beat later, so the layout briefly jitters.
  Safari has no `interactive-widget=resizes-content`. Anticipating the keyboard height
  (shrinking the frame on touchend) was tried and reverted: the field moved out from under
  the finger and the keyboard stopped opening. Real fix: a native shell (Capacitor
  `Keyboard.resize`).
- **Keyboard accessory bar** (arrows + checkmark) can't be hidden from the web.
- **Haptics:** only the native text-selection tick (used by the long-press menu).

## Open questions for Andrew

1. **Branding**: reuse Dolly's look (tokens, fonts) as-is, or give the coach its own palette/name? The tokens make this a small change either way.
2. **Models**: Haiku 5.5 for chat and Sonnet 5.5 for briefs, or Haiku for everything?
3. **Andrew's own use**: stay on claude.ai + the routine, or also use the web app (`POLL_USERS=andrew:app`)?
4. **Friend's plan**: does he have a training plan to give the agent (a document to paste into notes), or should the agent coach from his data alone in v1?
5. **Commit first**: the poller work in the working tree is uncommitted. Commit it before starting, so Phase 4 builds on a clean base.
