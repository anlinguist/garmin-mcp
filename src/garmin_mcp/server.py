"""MCP server entry point: streamable HTTP on 127.0.0.1 behind Caddy."""

from __future__ import annotations

import logging
import os
from typing import Any

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import SCOPE, GitHubOAuthProvider
from .config import Settings
from .logs import log_event, setup_logging
from .providers import GarminError
from .providers.unofficial import UnofficialProvider
from .registry import Registry
from .resilience import RateLimiter, TTLCache
from .secrets import get_secret
from .tokens import EncryptedFileTokenStore
from .tools import GarminService

logger = logging.getLogger(__name__)

INSTRUCTIONS = """Garmin Connect data for triathlon training (swim/bike/run/bricks, HR,
pace, power, HRV, sleep, training load, readiness).
- For "what did I do recently" use recent_activities first.
- Otherwise use search to find a garminconnect method, then execute it.
  Dates are YYYY-MM-DD. Bulky time series are dropped unless full=true.
- If a result says to re-run garmin-mcp-login, tell the user; don't retry."""


def _dev_user(settings: Settings) -> str | None:
    """DEV_NO_AUTH_USER: skip OAuth for local Inspector testing (localhost only)."""
    user = os.environ.get("DEV_NO_AUTH_USER", "").strip()
    if not user:
        return None
    if settings.public_host.split(":")[0] not in {"localhost", "127.0.0.1"} or settings.host != "127.0.0.1":
        raise RuntimeError("DEV_NO_AUTH_USER is only allowed with a localhost PUBLIC_BASE_URL")
    return user


def build_app(settings: Settings, service: GarminService,
              auth_provider: GitHubOAuthProvider | None, dev_user: str | None = None) -> Any:
    auth = None
    if auth_provider is not None:
        auth = AuthSettings(
            issuer_url=settings.issuer_url,
            resource_server_url=settings.mcp_url,
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
    mcp = MCPServer(
        "garmin",
        instructions=INSTRUCTIONS,
        auth_server_provider=auth_provider,
        auth=auth,
    )

    def current_user() -> str:
        if dev_user:
            return dev_user
        token = get_access_token()
        if token is None or auth_provider is None:
            raise ToolError("Not authenticated")
        user_id = auth_provider.user_for_subject(token.subject)
        if user_id is None or (token.claims or {}).get("user_id") != user_id:
            raise ToolError("Not authorized")
        return user_id

    def run(fn: Any, *args: Any) -> dict[str, Any]:
        try:
            return fn(*args)
        except GarminError as e:
            raise ToolError(str(e)) from None
        except Exception as e:
            log_event(logger, logging.ERROR, "tool_unexpected_error", error=type(e).__name__)
            raise ToolError(f"Internal error ({type(e).__name__})") from None

    ro = ToolAnnotations(readOnlyHint=True, openWorldHint=True)

    @mcp.tool(annotations=ro)
    def search(query: str, limit: int = 10) -> dict[str, Any]:
        """Find garminconnect client methods matching a free-text query (e.g. "hrv",
        "sleep", "training readiness", "activity splits", "power zones").
        Returns name, signature, summary and read/write access for each."""
        current_user()
        return run(service.search, query, limit)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=not settings.allow_writes,
                                          destructiveHint=settings.allow_writes,
                                          openWorldHint=True))
    def execute(method: str, args: dict[str, Any] | None = None, full: bool = False) -> dict[str, Any]:
        """Call one garminconnect method by name with keyword args (a JSON object),
        e.g. method="get_hrv_data", args={"cdate": "2026-09-29"}. Use search first
        to find names and parameters. Only allowlisted methods run; write methods
        are disabled unless the server enables them. full=true keeps bulky
        time-series arrays (large); responses are size-capped either way."""
        user = current_user()
        return run(service.execute, user, method, args, full)

    @mcp.tool(annotations=ro)
    def recent_activities(days: int = 7, sport: str | None = None) -> dict[str, Any]:
        """Compact list of recent activities (default last 7 days). sport filter:
        run, bike, swim, brick/multisport, strength, walk, hike. Includes date,
        duration, distance, HR, pace/speed, power, swim pool/strokes, training
        effect and activity_id (use with execute, e.g. get_activity_splits)."""
        user = current_user()
        return run(service.recent_activities, user, days, sport)

    if auth_provider is not None:
        @mcp.custom_route("/github/callback", methods=["GET"])
        async def github_callback(request: Request) -> Response:
            return await auth_provider.github_callback(request)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    local = settings.public_host.split(":")[0] in {"localhost", "127.0.0.1"}
    origins = ["https://claude.ai", "https://claude.com", settings.public_base_url]
    hosts = [settings.public_host]
    if local:
        hosts += ["localhost:*", "127.0.0.1:*"]
        origins += ["http://localhost:*", "http://127.0.0.1:*"]
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        host=settings.host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        ),
    )


def create_app() -> tuple[Settings, Any]:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    fernet_key = get_secret(settings.fernet_key_secret_name, "GARMIN_MCP_FERNET_KEY",
                            settings.gcp_project)
    token_store = EncryptedFileTokenStore(settings.token_dir, fernet_key)
    provider = UnofficialProvider(
        token_store, impersonate_api_tls=settings.garmin_api_tls_impersonation
    )
    registry = Registry()
    service = GarminService(
        provider, registry,
        allow_writes=settings.allow_writes,
        cache=TTLCache(settings.cache_ttl_seconds),
        limiter=RateLimiter(settings.rate_limit_per_minute),
        max_response_bytes=settings.max_response_bytes,
    )
    dev_user = _dev_user(settings)
    auth_provider = None
    if dev_user is None:
        if not settings.github_client_id or not settings.allowed_github_users:
            raise RuntimeError("GITHUB_CLIENT_ID and ALLOWED_GITHUB_USERS are required")
        gh_secret = get_secret(settings.github_client_secret_name, "GITHUB_CLIENT_SECRET",
                               settings.gcp_project)
        state_store = EncryptedFileTokenStore(settings.oauth_state_dir, fernet_key)
        auth_provider = GitHubOAuthProvider(settings, gh_secret, state_store)
    else:
        log_event(logger, logging.WARNING, "dev_no_auth_enabled", user_id=dev_user)
    counts: dict[str, int] = {}
    for m in registry.all():
        counts[m.access.value] = counts.get(m.access.value, 0) + 1
    log_event(logger, logging.INFO, "startup", base_url=settings.public_base_url,
              allow_writes=settings.allow_writes, methods=counts,
              allowed_users=len(settings.allowed_github_users))
    return settings, build_app(settings, service, auth_provider, dev_user)


def main() -> None:
    settings, app = create_app()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        server_header=False,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
