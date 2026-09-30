"""Provider interface: how we get an authenticated Garmin client for a user.

Swapping in Garmin's official Connect Developer Program API later means adding
another GarminProvider implementation; tools only depend on this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------- errors ---
class GarminError(Exception):
    """Base for errors whose message is safe to show to the MCP caller."""

    user_message = "Garmin request failed."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.user_message)


class ReauthRequired(GarminError):
    def __init__(self, user_id: str, reason: str = "tokens missing or rejected") -> None:
        super().__init__(
            f"Garmin authorization for user '{user_id}' is not usable ({reason}). "
            f"Re-run on the server: sudo garmin-mcp-login --user {user_id}"
        )


class RateLimited(GarminError):
    user_message = "Rate limited. Wait a bit before retrying."


class CloudflareBlocked(GarminError):
    user_message = (
        "Garmin's Cloudflare edge blocked or challenged the request. This is not "
        "an auth problem; wait and retry later. If it persists, check for a "
        "garminconnect update."
    )


class UpstreamError(GarminError):
    user_message = "Garmin returned an error."


class NotFound(GarminError):
    user_message = "Garmin says the requested resource does not exist."


# ---------------------------------------------------------------- login ----
class LoginStatus(Enum):
    DONE = "done"
    NEEDS_MFA = "needs_mfa"


@dataclass
class LoginHandle:
    """Opaque in-process state between login_start and login_complete."""

    user_id: str
    state: Any = field(repr=False)


@dataclass
class LoginResult:
    status: LoginStatus
    handle: LoginHandle | None = None


# ---------------------------------------------------------------- session --
class GarminSession(ABC):
    """An authenticated, per-user client handle."""

    user_id: str

    @abstractmethod
    def call(self, fn: Callable[..., Any], kwargs: dict[str, Any], *, retry: bool) -> Any:
        """Invoke ``fn(client, **kwargs)`` with backoff and error translation.

        ``fn`` is an unbound function from the method registry, never a name
        looked up at call time.
        """


class GarminProvider(ABC):
    @abstractmethod
    def get_client(self, user_id: str) -> GarminSession:
        """Return an authenticated session or raise ReauthRequired."""

    @abstractmethod
    def login_start(self, user_id: str, email: str, password: str) -> LoginResult:
        """Begin interactive login. Must never be retried automatically."""

    @abstractmethod
    def login_complete(self, handle: LoginHandle, mfa_code: str) -> None:
        """Finish an MFA login and persist tokens."""

    def invalidate(self, user_id: str) -> None:  # noqa: B027 - optional hook
        """Drop any cached in-memory session for the user."""
