# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal remote MCP server (streamable HTTP + MCP OAuth 2.1, GitHub as the identity provider) that gives claude.ai read access to Garmin Connect via the **unofficial** `garminconnect` library. It runs on a single GCP VM behind Caddy (optionally behind Cloudflare). README.md has the full setup, deploy, and re-auth runbook.

## Commands

```bash
uv sync                                        # Python 3.12, deps pinned exactly in pyproject + uv.lock
uv run pytest                                  # all tests (Garmin and GitHub are mocked)
uv run pytest tests/test_registry.py           # one file
uv run pytest tests/test_auth.py::test_full_flow_and_tool_call   # one test
uv run pip-audit                               # dependency audit (dev group)

# Local server without OAuth (refused unless PUBLIC_BASE_URL is localhost and HOST is 127.0.0.1)
export GARMIN_MCP_FERNET_KEY=$(uv run python -c 'from garmin_mcp.tokens import generate_key; print(generate_key())')
export PUBLIC_BASE_URL=http://localhost:8000 DATA_DIR=./data
DEV_NO_AUTH_USER=andrew uv run garmin-mcp       # then MCP Inspector -> Streamable HTTP -> http://localhost:8000/mcp

./deploy.sh              # upload working tree over IAP, uv sync --frozen, switch release, restart, health check
./deploy.sh --rollback   # previous release
uv lock                  # only when dependencies change; deploy refuses without uv.lock
```

There is no linter/formatter configured. `scripts/smoke_live.py` hits the real Garmin account and is run on the VM only, never in CI.

## Architecture

Request path: `server.py` (MCPServer tools) → `tools.GarminService` (transport-independent; this is what unit tests target) → `registry.Registry` (validate method + args) → `GarminProvider.get_client(user_id)` → `GarminSession.call(fn, kwargs, retry=...)` → `shaping.shape()` on the result.

- **Three tools only**: `search`, `execute`, `recent_activities`. `execute` is a generic dispatcher over the whole `garminconnect.Garmin` class rather than one hand-written tool per endpoint.
- **Registry security model** (`registry.py`, see its module docstring). The method index is built at startup by introspecting `vars(Garmin)`, and dispatch stores the *unbound function object*, so nothing does `getattr` on user input. Classification is default-deny: READ requires a `get_`/`count_` prefix **and** source with no POST/PUT/DELETE/PATCH; WRITE requires an explicit write prefix; everything else is BLOCKED. Path-like params, variadic signatures, binary returns, and `ALWAYS_BLOCKED` (login, raw `connectapi`, GraphQL, file uploads) can never be called. Writes also need `GARMIN_ALLOW_WRITES=true`. Keep this posture when changing the registry. Upgrading `garminconnect` changes the method surface, so re-check classification.
- **Provider abstraction** (`providers/base.py`): tools depend only on `GarminProvider`/`GarminSession` and the `GarminError` hierarchy. `GarminError` messages are shown to the MCP caller; any other exception becomes a generic `Internal error (TypeName)` in `server.run()`. `providers/official.py` is a stub for a future swap via `server.create_app`.
- **Unofficial provider** (`providers/unofficial.py`): tokens are passed to garminconnect as an inline JSON blob, never as a tokenstore path, so the library never writes plaintext. After each call, refreshed tokens are diffed against the last persisted copy and written back through `EncryptedFileTokenStore`. Cloudflare challenges are detected by instrumenting the requests session and are surfaced as `CloudflareBlocked`, which is distinct from `ReauthRequired`. Optional TLS impersonation exists for the datacenter-IP 403 problem.
- **Login is never automatic.** `login_start`/`login_complete` run only from `garmin-mcp-login` (`login_cli.py`), interactively, with one attempt. Garmin rate-limits logins per IP and fingerprint, so don't add retries or automatic re-login. `ReauthRequired` tells the user to re-run the CLI on the server.
- **Auth** (`auth.py`, see its docstring for the flow): the server is its own OAuth AS (DCR limited to allowlisted redirect URIs, PKCE, RFC 8707 resource check) and delegates identity to GitHub. The GitHub token is used once to read the numeric user ID and then discarded. `ALLOWED_GITHUB_USERS` maps `<numeric_github_id>:<user_id>`, using numeric IDs because logins can be re-registered. Access tokens and pending auths live in memory; clients and refresh-token hashes persist in a Fernet-encrypted state file. `server.current_user()` re-checks the token's `user_id` claim against the allowlist on every call.
- **Per-user isolation**: tokens, `TTLCache` entries, and `RateLimiter` buckets are all keyed by `user_id`. Writes and `ReauthRequired` clear that user's cache.
- **Secrets** (`secrets.py`): Secret Manager in production, with env-var fallback (`GARMIN_MCP_FERNET_KEY`, `GITHUB_CLIENT_SECRET`) for local use.
- **Config**: `config.Settings.from_env()`. Every variable is documented in `.env.example`. `PUBLIC_BASE_URL` must be a bare https origin (localhost excepted) and derives the issuer, `/mcp`, and `/github/callback` URLs.
- **Logging**: use `logs.log_event(logger, level, event, **fields)`, which emits JSON lines. Never log tokens, passwords, or response payloads.

## Library notes

- `mcp==2.2.0` uses `mcp.server.mcpserver.MCPServer`, not the older `FastMCP` API. The app runs stateless with JSON responses and DNS-rebinding protection (allowed hosts/origins are set in `build_app`).
- The HTTP client is `httpx2`, not `httpx`.
- Async tests don't use pytest-asyncio. `tests/test_auth.py` wraps them with a local `@run` decorator (`anyio.run` inside the app lifespan) and drives the real ASGI app through `httpx2.ASGITransport` with GitHub mocked by `MockTransport`.
- Shared fakes are in `tests/conftest.py`. `FakeGarmin` includes deliberately dangerous methods (`get_sneaky`, `get_file`, `connectapi`, ...) to exercise registry blocking. `make_service()` builds a `GarminService` around the fakes.
