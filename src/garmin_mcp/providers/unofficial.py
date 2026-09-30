"""GarminProvider backed by the unofficial ``garminconnect`` library (0.3.x).

Token handling:
  * Tokens are passed to garminconnect as an inline JSON string (``login(blob)``),
    so the library never gets a tokenstore *path* and never writes plaintext
    tokens to disk.
  * garminconnect refreshes the DI token in-process. After every call we
    compare ``client.dumps()`` with the last persisted value and write changes
    back through the (encrypted, atomic) TokenStore.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from ..logs import log_event
from ..tokens import TokenStore, validate_user_id
from .base import (
    CloudflareBlocked,
    GarminError,
    GarminProvider,
    GarminSession,
    LoginHandle,
    LoginResult,
    LoginStatus,
    NotFound,
    RateLimited,
    ReauthRequired,
    UpstreamError,
)

try:  # NotFound subclass added in 0.3.x
    from garminconnect import GarminConnectNotFoundError
except ImportError:  # pragma: no cover
    GarminConnectNotFoundError = GarminConnectConnectionError  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

_STATUS_RE = re.compile(r"\b(?:API Error|HTTP|error \()\s*(\d{3})\b")


# --------------------------------------------------------- Cloudflare probe --
class _ResponseProbe(threading.local):
    """Per-thread record of the last Garmin API response's status/edge markers."""

    status: int | None = None
    cloudflare: bool = False
    retry_after: float | None = None

    def reset(self) -> None:
        self.status, self.cloudflare, self.retry_after = None, False, None


_probe = _ResponseProbe()


def _looks_like_cloudflare(status: int, headers: Any) -> bool:
    get = headers.get
    if (get("cf-mitigated") or "").lower() == "challenge":
        return True
    if status in (403, 429, 503):
        server = (get("server") or "").lower()
        ctype = (get("content-type") or "").lower()
        # Garmin's own API errors are JSON; Cloudflare block/challenge pages are HTML.
        return "cloudflare" in server and "text/html" in ctype
    return False


def _instrument_session(session: Any) -> None:
    """Wrap ``session.request`` to record status + Cloudflare markers.

    Works for both requests.Session and curl_cffi Session. Only headers and
    status are inspected; bodies are never logged.
    """
    if session is None or getattr(session, "_garmin_mcp_probe", False):
        return
    original = session.request

    def request(method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        resp = original(method, url, *args, **kwargs)
        try:
            status = int(resp.status_code)
            _probe.status = status
            _probe.cloudflare = _looks_like_cloudflare(status, resp.headers)
            ra = resp.headers.get("retry-after")
            _probe.retry_after = float(ra) if ra and ra.isdigit() else None
        except Exception:  # never let instrumentation break a request
            pass
        return resp

    session.request = request
    session._garmin_mcp_probe = True


def make_garmin(
    email: str | None = None,
    password: str | None = None,
    *,
    return_on_mfa: bool = False,
    impersonate_api_tls: bool = False,
) -> Garmin:
    """Construct a Garmin client with our instrumentation applied.

    ``retry_attempts=0``: we do our own backoff (the library never retries 429).

    ``impersonate_api_tls``: garminconnect uses curl_cffi TLS impersonation for
    *login* but plain ``requests`` for API calls. From datacenter IPs Garmin's
    edge may reject that fingerprint with 403 on every API call
    (python-garminconnect#444). Enabling this swaps the library's private API
    session for a curl_cffi Session (the library's own dependency). File
    uploads are not supported on that path, which is fine: uploads are blocked.
    """
    g = Garmin(
        email,
        password,
        return_on_mfa=return_on_mfa,
        retry_attempts=0,
    )
    client = g.client
    if impersonate_api_tls:
        from curl_cffi import requests as cffi_requests

        client._api_session = cffi_requests.Session(impersonate="chrome")
    _instrument_session(getattr(client, "_api_session", None))
    return g


def _status_of(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status
    m = _STATUS_RE.search(str(exc))
    return int(m.group(1)) if m else None


def translate_error(exc: BaseException, user_id: str) -> GarminError:
    """Map garminconnect exceptions to caller-safe errors (no raw payloads)."""
    if isinstance(exc, GarminError):
        return exc
    if _probe.cloudflare:
        return CloudflareBlocked()
    if isinstance(exc, GarminConnectAuthenticationError):
        return ReauthRequired(user_id, "Garmin rejected the token")
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited("Garmin rate limited the request (429). Try again in a few minutes.")
    if isinstance(exc, GarminConnectNotFoundError):
        return NotFound()
    status = _status_of(exc)
    if status == 401:
        return ReauthRequired(user_id, "Garmin returned 401")
    if status == 403:
        return UpstreamError(
            "Garmin returned 403 Forbidden. If every call fails this way, the "
            "token may be rejected from this network; see README "
            "(GARMIN_API_TLS_IMPERSONATION)."
        )
    if status == 429:
        return RateLimited("Garmin rate limited the request (429).")
    if isinstance(exc, ValueError | TypeError):
        # Library-side argument validation; message is about the input, not data.
        return GarminError(f"Invalid arguments: {str(exc)[:200]}")
    if status:
        return UpstreamError(f"Garmin returned HTTP {status}.")
    return UpstreamError(f"Garmin request failed ({type(exc).__name__}).")


def _retryable(exc: BaseException) -> bool:
    if _probe.cloudflare:
        return False  # hammering a challenge makes it worse
    if isinstance(exc, GarminConnectTooManyRequestsError):
        return True
    if isinstance(exc, GarminConnectAuthenticationError | GarminConnectNotFoundError):
        return False
    status = _status_of(exc)
    if status is not None:
        return status == 429 or 500 <= status < 600
    import requests

    return isinstance(exc, requests.ConnectionError | requests.Timeout)


# ------------------------------------------------------------------ session --
class UnofficialSession(GarminSession):
    def __init__(
        self,
        user_id: str,
        garmin: Garmin,
        store: TokenStore,
        persisted_blob: str,
        *,
        max_attempts: int = 3,
        base_delay: float = 2.0,
        max_delay: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_id = user_id
        self._garmin = garmin
        self._store = store
        self._persisted = persisted_blob
        self._lock = threading.Lock()
        self._max_attempts = max_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep

    @property
    def persisted_blob(self) -> str:
        return self._persisted

    def _persist_if_refreshed(self) -> None:
        try:
            current = self._garmin.client.dumps()
        except Exception:
            return
        if current == self._persisted or '"di_token": null' in current:
            return
        # If the login CLI wrote new tokens while this session was live, don't
        # clobber them with this (older) session's refresh; the provider will
        # rebuild the session from disk on the next call.
        if self._store.load(self.user_id) != self._persisted:
            log_event(logger, logging.INFO, "garmin_tokens_changed_externally", user_id=self.user_id)
            return
        self._store.save(self.user_id, current)
        self._persisted = current
        log_event(logger, logging.INFO, "garmin_tokens_refreshed", user_id=self.user_id)

    def call(self, fn: Callable[..., Any], kwargs: dict[str, Any], *, retry: bool) -> Any:
        attempts = self._max_attempts if retry else 1
        with self._lock:  # serialize per user: keeps token refresh + probe coherent
            try:
                for attempt in range(attempts):
                    _probe.reset()
                    try:
                        return fn(self._garmin, **kwargs)
                    except Exception as exc:
                        if _probe.cloudflare:
                            log_event(
                                logger, logging.WARNING, "garmin_cloudflare_block",
                                user_id=self.user_id, status=_probe.status,
                                method=getattr(fn, "__name__", "?"),
                            )
                        if attempt + 1 < attempts and _retryable(exc):
                            delay = _probe.retry_after or min(
                                self._max_delay, self._base_delay * (2**attempt)
                            )
                            delay = min(self._max_delay, delay) * (0.5 + random.random() / 2)
                            log_event(
                                logger, logging.WARNING, "garmin_retry",
                                user_id=self.user_id, attempt=attempt + 1,
                                status=_probe.status or _status_of(exc),
                                delay_s=round(delay, 1),
                            )
                            self._sleep(delay)
                            continue
                        err = translate_error(exc, self.user_id)
                        log_event(
                            logger, logging.WARNING, "garmin_call_failed",
                            user_id=self.user_id, method=getattr(fn, "__name__", "?"),
                            error=type(err).__name__, status=_probe.status,
                        )
                        raise err from None
            finally:
                self._persist_if_refreshed()
        raise AssertionError("unreachable")  # pragma: no cover


# ----------------------------------------------------------------- provider --
class UnofficialProvider(GarminProvider):
    def __init__(self, store: TokenStore, *, impersonate_api_tls: bool = False) -> None:
        self._store = store
        self._impersonate = impersonate_api_tls
        self._sessions: dict[str, UnofficialSession] = {}
        self._lock = threading.Lock()

    def invalidate(self, user_id: str) -> None:
        with self._lock:
            self._sessions.pop(user_id, None)

    def get_client(self, user_id: str) -> UnofficialSession:
        validate_user_id(user_id)
        with self._lock:
            blob = self._store.load(user_id)
            sess = self._sessions.get(user_id)
            if sess is not None and blob == sess.persisted_blob:
                return sess
            self._sessions.pop(user_id, None)  # tokens changed on disk (re-login) or first use
            if not blob:
                raise ReauthRequired(user_id, "no Garmin tokens stored")
            garmin = make_garmin(impersonate_api_tls=self._impersonate)
            _probe.reset()
            try:
                # Inline JSON => token resume only. With no password set the
                # library cannot fall back to an SSO login, so this never
                # triggers a credential login from the server.
                garmin.login(blob)
            except Exception as exc:
                if _probe.cloudflare:
                    log_event(logger, logging.WARNING, "garmin_cloudflare_block",
                              user_id=user_id, status=_probe.status, phase="resume")
                # garmin.login() maps token rejection to AuthenticationError
                # (and wraps other failures as ConnectionError/"Login failed").
                err = translate_error(exc, user_id)
                log_event(logger, logging.WARNING, "garmin_resume_failed",
                          user_id=user_id, error=type(err).__name__)
                raise err from None
            sess = UnofficialSession(user_id, garmin, self._store, blob)
            sess._persist_if_refreshed()  # login() may have refreshed proactively
            self._sessions[user_id] = sess
            log_event(logger, logging.INFO, "garmin_session_resumed", user_id=user_id)
            return sess

    # -- interactive login (CLI only; the server never calls these) ----------
    def login_start(self, user_id: str, email: str, password: str) -> LoginResult:
        validate_user_id(user_id)
        garmin = make_garmin(email, password, return_on_mfa=True,
                             impersonate_api_tls=self._impersonate)
        status, _ = garmin.login()  # single attempt; the library tries its strategies once
        if status == "needs_mfa":
            return LoginResult(LoginStatus.NEEDS_MFA, LoginHandle(user_id, garmin))
        self._save_after_login(user_id, garmin)
        return LoginResult(LoginStatus.DONE)

    def login_complete(self, handle: LoginHandle, mfa_code: str) -> None:
        garmin: Garmin = handle.state
        garmin.resume_login(None, mfa_code.strip())
        self._save_after_login(handle.user_id, garmin)

    def _save_after_login(self, user_id: str, garmin: Garmin) -> None:
        garmin.password = None
        blob = garmin.client.dumps()
        if '"di_token": null' in blob:
            # Login succeeded via a JWT_WEB-only strategy; garminconnect 0.3.x
            # only persists DI tokens, so this session can't be resumed later.
            raise GarminError(
                "Login succeeded but produced no persistable DI token "
                "(web-session fallback). Try again later."
            )
        self._store.save(user_id, blob)
        self.invalidate(user_id)
        log_event(logger, logging.INFO, "garmin_login_saved", user_id=user_id)
