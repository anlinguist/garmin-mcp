"""MCP server entry point: streamable HTTP on 127.0.0.1 behind Caddy."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .agent import Agent, AnthropicBackend
from .appstore import AppStore
from .garminlogin import LoginManager
from .proactive import AgentDelivery, Proactive, ProactiveThread
from .providers.fake import FakeGarminProvider, simulate_activity
from .auth import SCOPE, GitHubOAuthProvider
from .config import Settings
from .logs import log_event, setup_logging
from .providers import GarminError, GarminProvider
from .poller import Poller, PollerThread, RoutineClient, Schedule, StateStore, token_fingerprint
from .providers.unofficial import UnofficialProvider
from .registry import Registry
from .resilience import RateLimiter, TTLCache
from .secrets import get_secret
from .tokens import EncryptedFileTokenStore
from .tools import GarminService
from .webapp import build_app_routes
from .webauth import WebAuth
from .webchat import ChatRunner
from .webpush import WebPush

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
              auth_provider: GitHubOAuthProvider | None, dev_user: str | None = None,
              extra_routes: list[Any] | None = None) -> Any:
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
    app = mcp.streamable_http_app(
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
    if extra_routes:
        app.router.routes.extend(extra_routes)
    return app


def _fake_garmin(settings: Settings) -> bool:
    """RABBIT_FAKE_GARMIN=1: scripted logins and canned data, no Garmin network.

    Only for the Rabbit-only process: it would replace real data in the MCP server.
    """
    if os.environ.get("RABBIT_FAKE_GARMIN", "").strip() != "1":
        return False
    if settings.mcp_enabled:
        # Never swap fake data into the MCP server, and never fail its startup over it.
        log_event(logger, logging.ERROR, "rabbit_fake_garmin_ignored", reason="mcp_enabled")
        return False
    log_event(logger, logging.WARNING, "rabbit_fake_garmin_enabled")
    return True


def build_chat(settings: Settings, store: AppStore, service: GarminService) -> ChatRunner | None:
    """Coach agent; None (chat disabled, rest of the app works) without credentials."""
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key and settings.gcp_project:
        key = get_secret(settings.anthropic_api_key_secret_name, "ANTHROPIC_API_KEY",
                         settings.gcp_project)
    client = anthropic.AsyncAnthropic(api_key=key) if key else anthropic.AsyncAnthropic()
    backend = AnthropicBackend(client, settings.agent_model, effort=settings.agent_effort)
    agent = Agent(service, store, backend, daily_token_budget=settings.agent_daily_token_budget,
                  default_timezone=settings.poll_timezone)
    return ChatRunner(agent, max_concurrent=settings.agent_max_concurrent)


@dataclass
class RabbitParts:
    routes: list[Any]
    proactive: Proactive | None = None
    loop_holder: dict[str, Any] = field(default_factory=dict)


def build_rabbit(settings: Settings, fernet_key: str, token_store: EncryptedFileTokenStore,
                 dev_user: str | None, provider: GarminProvider | None = None,
                 on_tokens_changed: Any = None,
                 service: GarminService | None = None) -> RabbitParts:
    """Rabbit web app: routes, plus proactive messages in a Rabbit-only process."""
    if not settings.app_enabled:
        return RabbitParts([])
    store = AppStore(settings.app_dir, fernet_key)
    if dev_user is None:
        if not settings.google_client_id or not settings.allowed_google_users:
            raise RuntimeError("APP_ENABLED needs GOOGLE_CLIENT_ID and ALLOWED_GOOGLE_USERS")
        google_secret = get_secret(settings.google_client_secret_name, "GOOGLE_CLIENT_SECRET",
                                   settings.gcp_project)
    else:
        google_secret = ""
    auth = WebAuth(settings, store, google_secret, dev_user=dev_user)
    push = None
    if settings.vapid_subject:
        try:
            vapid_key = get_secret(settings.vapid_private_key_secret_name, "VAPID_PRIVATE_KEY",
                                   settings.gcp_project)
            push = WebPush(store, vapid_key, settings.vapid_subject)
        except Exception as e:  # push is optional; the app works without it
            log_event(logger, logging.ERROR, "push_disabled", error=type(e).__name__)
    fake = _fake_garmin(settings)
    if fake and not isinstance(provider, FakeGarminProvider):
        provider = FakeGarminProvider(token_store)
    chat = None
    if service is not None:
        try:
            chat = build_chat(settings, store, service)
        except Exception as e:  # chat is optional; sign-in, push and Garmin connect still work
            log_event(logger, logging.ERROR, "chat_disabled", error=type(e).__name__,
                      detail=str(e)[:200] if isinstance(e, RuntimeError) else None)

    parts = RabbitParts([])
    proactive = None
    if chat is not None and service is not None and not settings.mcp_enabled:
        users = sorted(set(settings.allowed_google_users.values()) | ({dev_user} if dev_user else set()))
        proactive = Proactive(
            service=service, store=store, token_store=token_store, users=lambda: users,
            deliver=AgentDelivery(chat, push, lambda: parts.loop_holder.get("loop")),
            notify=(lambda u, t, b: push.send(u, t, b)) if push else (lambda u, t, b: None),
            schedule=Schedule.parse(settings.poll_timezone, settings.poll_fast_hours,
                                    settings.poll_fast_minutes),
            state_dir=settings.data_dir / "poller", default_tz=settings.poll_timezone,
        )
    hook = on_tokens_changed or (lambda user_id: None)

    def tokens_changed(user_id: str) -> None:
        hook(user_id)
        if proactive is not None:
            proactive.on_connected(user_id)
    logins = LoginManager(provider, store, on_tokens_changed=tokens_changed) if provider else None

    dev = None
    if fake and proactive is not None:
        def simulate_workout(user_id: str) -> dict[str, Any]:
            proactive.poll(user_id)  # baseline first, so the new activity counts as new
            simulate_activity()
            return proactive.poll(user_id)
        dev = {"simulate_workout": simulate_workout,
               "brief_now": lambda user_id: proactive.send_brief(user_id).outcome.value}
    elif proactive is not None and os.environ.get("RABBIT_DEV_TOOLS") == "1":
        # Local phone testing on real Garmin (scripts/dev-rabbit.sh phone): only the brief,
        # run inside this process so Garmin tokens stay owned by one process. Never set in
        # production.
        dev = {"brief_now": lambda user_id: proactive.send_brief(user_id).outcome.value}
    log_event(logger, logging.INFO, "app_enabled", push=push is not None,
              garmin_connect=logins is not None, fake_garmin=fake, chat=chat is not None,
              proactive=proactive is not None,
              model=settings.agent_model if chat else None,
              allowed_users=len(settings.allowed_google_users))
    parts.routes = build_app_routes(settings, auth, token_store, push, logins, chat,
                                    fake_garmin=fake, dev=dev,
                                    default_tz=settings.poll_timezone)
    parts.proactive = proactive
    return parts


def build_web_routes(settings: Settings, fernet_key: str, token_store: EncryptedFileTokenStore,
                     dev_user: str | None, provider: GarminProvider | None = None,
                     on_tokens_changed: Any = None,
                     service: GarminService | None = None) -> list[Any]:
    """Rabbit routes only (used when /app is mounted inside the MCP process)."""
    return build_rabbit(settings, fernet_key, token_store, dev_user, provider,
                        on_tokens_changed, service).routes


def build_poller(settings: Settings, service: GarminService,
                 token_store: EncryptedFileTokenStore) -> PollerThread | None:
    """Background workout-sync poller; None unless POLL_USER_ID is set."""
    user = settings.poll_user_id
    if not user:
        return None
    routine = None
    if not settings.poll_dry_run:
        if not settings.routine_id:
            raise RuntimeError("ROUTINE_ID is required when POLL_USER_ID is set")
        token = get_secret(settings.routine_token_secret_name, "ROUTINE_TOKEN",
                           settings.gcp_project)
        routine = RoutineClient(settings.routine_id, token)
    state_dir = settings.poll_state_path.parent
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    def fetch_page(start: int, limit: int) -> list[dict[str, Any]]:
        return service.read_uncached(user, "get_activities", {"start": start, "limit": limit})

    poller = Poller(
        fetch_page, StateStore(settings.poll_state_path), routine,
        dry_run=settings.poll_dry_run,
        max_fires_per_day=settings.poll_max_fires_per_day,
        token_fingerprint=token_fingerprint(lambda: token_store.load(user)),
    )
    schedule = Schedule.parse(settings.poll_timezone, settings.poll_fast_hours,
                              settings.poll_fast_minutes)
    return PollerThread(poller, schedule, state_dir / "poller.lock")


def _attach_poller(app: Any, thread: PollerThread) -> None:
    """Run the poller for the app's lifetime, inside the MCP session manager's lifespan."""
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        async with inner(a) as state:
            thread.start()
            try:
                yield state
            finally:
                await anyio.to_thread.run_sync(thread.stop)

    app.router.lifespan_context = lifespan


def create_app() -> tuple[Settings, Any]:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    fernet_key = get_secret(settings.fernet_key_secret_name, "GARMIN_MCP_FERNET_KEY",
                            settings.gcp_project)
    token_store = EncryptedFileTokenStore(settings.token_dir, fernet_key)
    provider: GarminProvider = UnofficialProvider(
        token_store, impersonate_api_tls=settings.garmin_api_tls_impersonation
    )
    if _fake_garmin(settings):
        provider = FakeGarminProvider(token_store)
    registry = Registry()
    service = GarminService(
        provider, registry,
        allow_writes=settings.allow_writes,
        cache=TTLCache(settings.cache_ttl_seconds),
        limiter=RateLimiter(settings.rate_limit_per_minute),
        max_response_bytes=settings.max_response_bytes,
    )
    dev_user = _dev_user(settings)
    if not settings.mcp_enabled:
        # Rabbit-only process (its own service, port, data dir and user): no /mcp
        # endpoint at all, and config errors are fatal here since nothing else
        # depends on this process.
        rabbit = build_rabbit(settings, fernet_key, token_store, dev_user, provider,
                              service.cache.clear_user, service)
        if not rabbit.routes:
            raise RuntimeError("MCP_ENABLED=false needs APP_ENABLED=true")
        log_event(logger, logging.INFO, "startup", base_url=settings.public_base_url,
                  mode="app_only")
        return settings, build_web_only_app(rabbit.routes, rabbit)
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
    try:
        poller = build_poller(settings, service, token_store)
    except Exception as e:  # a broken poller config must not take the MCP server down
        log_event(logger, logging.ERROR, "poller_disabled", error=type(e).__name__,
                  detail=str(e)[:200] if isinstance(e, RuntimeError) else None)
        poller = None
    log_event(logger, logging.INFO, "startup", base_url=settings.public_base_url,
              allow_writes=settings.allow_writes, methods=counts,
              allowed_users=len(settings.allowed_github_users),
              poller=("off" if poller is None else
                      "dry_run" if settings.poll_dry_run else "on"))
    try:
        web_routes = build_web_routes(settings, fernet_key, token_store, dev_user)
    except Exception as e:  # a broken web-app config must not take the MCP server down
        log_event(logger, logging.ERROR, "app_disabled", error=type(e).__name__,
                  detail=str(e)[:200] if isinstance(e, RuntimeError) else None)
        web_routes = []
    app = build_app(settings, service, auth_provider, dev_user, web_routes)
    if poller is not None:
        _attach_poller(app, poller)
    return settings, app


def build_web_only_app(web_routes: list[Any], rabbit: RabbitParts | None = None) -> Any:
    """Starlette app with just /healthz and the Rabbit routes (no MCP, no OAuth AS).
    Starts the proactive-messages thread for the app's lifetime."""
    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    @contextlib.asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        thread = None
        if rabbit is not None and rabbit.proactive is not None:
            rabbit.loop_holder["loop"] = asyncio.get_running_loop()
            thread = ProactiveThread(rabbit.proactive)
            thread.start()
        try:
            yield
        finally:
            if thread is not None:
                await anyio.to_thread.run_sync(thread.stop)
    return Starlette(routes=[Route("/healthz", healthz, methods=["GET"]), *web_routes],
                     lifespan=lifespan)


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
