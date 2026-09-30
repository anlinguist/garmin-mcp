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
        )
