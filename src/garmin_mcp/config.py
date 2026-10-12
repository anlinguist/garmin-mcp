"""Settings loaded from the environment (systemd EnvironmentFile in production)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.environ.get(name, "").split(",") if p.strip()]


def _parse_user_map(entries: list[str]) -> dict[str, str]:
    """Parse ``<github_numeric_id>:<user_id>`` entries.

    Numeric ids, not logins: GitHub logins can be renamed and re-registered by
    someone else, ids never change. Find yours with ``gh api users/<login> --jq .id``.
    """
    from .tokens import validate_user_id

    out: dict[str, str] = {}
    for entry in entries:
        gh_id, sep, user_id = entry.partition(":")
        gh_id, user_id = gh_id.strip(), user_id.strip()
        if not sep or not gh_id.isdigit() or not user_id:
            raise RuntimeError(
                f"ALLOWED_GITHUB_USERS entry {entry!r} must be <numeric_github_id>:<user_id>"
            )
        out[gh_id] = validate_user_id(user_id)
    return out


def _parse_email_map(entries: list[str]) -> dict[str, str]:
    """Parse ``<email>:<user_id>`` entries for the Rabbit web app.

    Google's stable ``sub`` isn't known in advance, so the allowlist is by
    verified email and the ``sub`` is pinned on first sign-in (see webauth).
    """
    from .tokens import validate_user_id

    out: dict[str, str] = {}
    for entry in entries:
        email, sep, user_id = entry.rpartition(":")
        email, user_id = email.strip().lower(), user_id.strip()
        if not sep or "@" not in email or not user_id:
            raise RuntimeError(f"ALLOWED_GOOGLE_USERS entry {entry!r} must be <email>:<user_id>")
        out[email] = validate_user_id(user_id)
    return out


@dataclass(frozen=True)
class Settings:
    public_base_url: str
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("/var/lib/garmin-mcp")

    # GitHub OAuth (upstream identity provider)
    github_client_id: str = ""
    github_client_secret_name: str = ""  # Secret Manager secret id
    # GitHub numeric user id -> internal user_id
    allowed_github_users: dict[str, str] = field(default_factory=dict)
    # Exact redirect URIs DCR clients may register (claude.ai's callback by default)
    oauth_allowed_redirect_uris: tuple[str, ...] = ("https://claude.ai/api/mcp/auth_callback",)
    # Also allow http://localhost / 127.0.0.1 redirects (MCP Inspector, Claude Code)
    oauth_allow_loopback_redirects: bool = True

    # Secrets
    gcp_project: str = ""
    fernet_key_secret_name: str = ""

    # Behaviour
    allow_writes: bool = False
    garmin_api_tls_impersonation: bool = False
    cache_ttl_seconds: int = 300
    rate_limit_per_minute: int = 30
    max_response_bytes: int = 60_000
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 30 * 24 * 3600
    log_level: str = "INFO"

    # Workout-sync poller (off unless POLL_USER_ID is set)
    poll_user_id: str = ""
    routine_id: str = ""
    routine_token_secret_name: str = ""
    poll_dry_run: bool = False
    poll_timezone: str = "America/Denver"
    poll_fast_hours: str = "5-9"
    poll_fast_minutes: int = 5
    poll_max_fires_per_day: int = 10

    # MCP endpoint + GitHub OAuth AS. false = Rabbit-only process (own service/port/data dir).
    mcp_enabled: bool = True

    # Rabbit web app (off unless APP_ENABLED)
    app_enabled: bool = False
    google_client_id: str = ""
    google_client_secret_name: str = ""
    # verified Google email (lowercase) -> internal user_id
    allowed_google_users: dict[str, str] = field(default_factory=dict)
    app_session_ttl_seconds: int = 90 * 24 * 3600
    vapid_private_key_secret_name: str = ""
    vapid_subject: str = ""  # mailto: contact sent to push services

    # Rabbit coach agent (Claude API)
    anthropic_api_key_secret_name: str = ""
    agent_model: str = "claude-haiku-5-5"
    agent_effort: str = "low"
    agent_daily_token_budget: int = 2_000_000
    agent_max_concurrent: int = 2

    @property
    def issuer_url(self) -> str:
        return self.public_base_url

    @property
    def mcp_url(self) -> str:
        return f"{self.public_base_url}/mcp"

    @property
    def github_callback_url(self) -> str:
        return f"{self.public_base_url}/github/callback"

    @property
    def public_host(self) -> str:
        return urlparse(self.public_base_url).netloc

    @property
    def token_dir(self) -> Path:
        return self.data_dir / "tokens"

    @property
    def oauth_state_dir(self) -> Path:
        return self.data_dir / "oauth"

    @property
    def app_dir(self) -> Path:
        return self.data_dir / "app"

    @property
    def google_callback_url(self) -> str:
        return f"{self.public_base_url}/app/auth/google/callback"

    @property
    def is_https(self) -> bool:
        return self.public_base_url.startswith("https://")

    @property
    def poll_state_path(self) -> Path:
        return self.data_dir / "poller" / "state.json"

    @classmethod
    def from_env(cls) -> Settings:
        base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if not base:
            raise RuntimeError("PUBLIC_BASE_URL is required")
        parsed = urlparse(base)
        local = parsed.hostname in {"localhost", "127.0.0.1"}
        if parsed.scheme != "https" and not local:
            raise RuntimeError("PUBLIC_BASE_URL must be https (except localhost)")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise RuntimeError("PUBLIC_BASE_URL must be a bare origin")
        return cls(
            public_base_url=base,
            host=os.environ.get("HOST", "127.0.0.1"),
            port=_int("PORT", 8000),
            data_dir=Path(os.environ.get("DATA_DIR", "/var/lib/garmin-mcp")),
            github_client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
            github_client_secret_name=os.environ.get(
                "GITHUB_CLIENT_SECRET_NAME", "garmin-mcp-github-client-secret"
            ),
            allowed_github_users=_parse_user_map(_list("ALLOWED_GITHUB_USERS")),
            gcp_project=os.environ.get("GCP_PROJECT", ""),
            fernet_key_secret_name=os.environ.get(
                "FERNET_KEY_SECRET_NAME", "garmin-mcp-fernet-key"
            ),
            oauth_allowed_redirect_uris=tuple(
                _list("OAUTH_ALLOWED_REDIRECT_URIS")
                or ["https://claude.ai/api/mcp/auth_callback"]
            ),
            oauth_allow_loopback_redirects=_bool("OAUTH_ALLOW_LOOPBACK_REDIRECTS", True),
            allow_writes=_bool("GARMIN_ALLOW_WRITES", False),
            garmin_api_tls_impersonation=_bool("GARMIN_API_TLS_IMPERSONATION", False),
            cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 300),
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30),
            max_response_bytes=_int("MAX_RESPONSE_BYTES", 60_000),
            access_token_ttl_seconds=_int("ACCESS_TOKEN_TTL_SECONDS", 3600),
            refresh_token_ttl_seconds=_int("REFRESH_TOKEN_TTL_SECONDS", 30 * 24 * 3600),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            poll_user_id=os.environ.get("POLL_USER_ID", "").strip(),
            routine_id=os.environ.get("ROUTINE_ID", "").strip(),
            routine_token_secret_name=os.environ.get(
                "ROUTINE_TOKEN_SECRET_NAME", "garmin-mcp-routine-token"
            ),
            poll_dry_run=_bool("POLL_DRY_RUN", False),
            poll_timezone=os.environ.get("POLL_TIMEZONE", "America/Denver").strip(),
            poll_fast_hours=os.environ.get("POLL_FAST_HOURS", "5-9"),
            poll_fast_minutes=_int("POLL_FAST_MINUTES", 5),
            poll_max_fires_per_day=_int("POLL_MAX_FIRES_PER_DAY", 10),
            mcp_enabled=_bool("MCP_ENABLED", True),
            app_enabled=_bool("APP_ENABLED", False),
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
            google_client_secret_name=os.environ.get(
                "GOOGLE_CLIENT_SECRET_NAME", "garmin-mcp-google-client-secret"
            ),
            allowed_google_users=_parse_email_map(_list("ALLOWED_GOOGLE_USERS")),
            app_session_ttl_seconds=_int("APP_SESSION_TTL_SECONDS", 90 * 24 * 3600),
            vapid_private_key_secret_name=os.environ.get(
                "VAPID_PRIVATE_KEY_SECRET_NAME", "garmin-mcp-vapid-private-key"
            ),
            vapid_subject=os.environ.get("VAPID_SUBJECT", "").strip(),
            anthropic_api_key_secret_name=os.environ.get(
                "ANTHROPIC_API_KEY_SECRET_NAME", "garmin-mcp-anthropic-api-key"
            ),
            agent_model=os.environ.get("AGENT_MODEL", "claude-haiku-5-5").strip(),
            agent_effort=os.environ.get("AGENT_EFFORT", "low").strip(),
            agent_daily_token_budget=_int("AGENT_DAILY_TOKEN_BUDGET", 2_000_000),
            agent_max_concurrent=_int("AGENT_MAX_CONCURRENT", 2),
        )
