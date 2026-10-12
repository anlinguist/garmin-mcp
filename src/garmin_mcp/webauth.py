"""Google sign-in and cookie sessions for the Rabbit web app.

Flow:
  1. ``GET /app/auth/google/start`` stores a random ``state`` + PKCE verifier in
     memory, sets a short-lived cookie holding the same ``state`` (binds the
     login to this browser: login CSRF / session fixation), and redirects to
     Google with ``openid email profile`` (all non-sensitive scopes).
  2. ``GET /app/auth/google/callback`` checks state against both the pending
     entry and the cookie, exchanges the code (server-side, with PKCE), reads
     the OIDC userinfo once and discards Google's tokens. The verified email
     must be allowlisted; Google's ``sub`` is pinned to the user_id on first
     sign-in and must match afterwards (emails can be reassigned, ``sub`` can't).
  3. A random session token goes in a ``__Host-`` cookie (HttpOnly, Secure,
     SameSite=Lax). Only its SHA-256 is stored, encrypted, so sessions survive
     restarts. Every request re-checks the allowlist.

Mutating API calls additionally require ``Origin == PUBLIC_BASE_URL`` and a JSON
content type (see ``guard``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx2
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .appstore import AppStore
from .config import Settings
from .logs import log_event

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

PENDING_TTL = 600
MAX_PENDING = 50
LAST_SEEN_WRITE_INTERVAL = 3600  # persist last_seen at most hourly per session


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def _is_google_photo(url: str) -> bool:
    """Profile photos are only ever loaded from Google's image CDN (see the app CSP)."""
    p = urlparse(url)
    return p.scheme == "https" and p.hostname == "lh3.googleusercontent.com" and len(url) < 1024


@dataclass
class _Pending:
    verifier: str
    created: float


class WebAuth:
    def __init__(self, settings: Settings, store: AppStore, google_client_secret: str,
                 *, dev_user: str | None = None, http: httpx2.AsyncClient | None = None,
                 clock: Any = time.time) -> None:
        self.s = settings
        self._secret = google_client_secret
        self._dev_user = dev_user
        self._http = http
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._store = store
        self._sessions_doc = store.global_doc("sessions")
        self._identities_doc = store.global_doc("identities")
        self._sessions: dict[str, dict[str, Any]] = self._sessions_doc.load()
        secure = settings.is_https
        # __Host- requires Secure; plain http is only allowed for localhost dev.
        self.session_cookie = "__Host-rabbit_session" if secure else "rabbit_session"
        self.state_cookie = "__Host-rabbit_oauth" if secure else "rabbit_oauth"
        self._secure = secure

    # ------------------------------------------------------------- helpers --
    def _allowed_user_ids(self) -> set[str]:
        return set(self.s.allowed_google_users.values())

    def _cookie(self, resp: Response, name: str, value: str, max_age: int) -> None:
        resp.set_cookie(name, value, max_age=max_age, path="/", secure=self._secure,
                        httponly=True, samesite="lax")

    def _fail(self, reason: str) -> RedirectResponse:
        resp = RedirectResponse(f"/app/?auth_error={reason}", status_code=302)
        resp.delete_cookie(self.state_cookie, path="/", secure=self._secure, httponly=True,
                           samesite="lax")
        return resp

    # --------------------------------------------------------------- login --
    async def start(self, request: Request) -> Response:
        state = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        now = self._clock()
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items()
                             if now - v.created < PENDING_TTL}
            if len(self._pending) >= MAX_PENDING:
                return self._fail("busy")
            self._pending[state] = _Pending(verifier, now)
        query = urlencode({
            "client_id": self.s.google_client_id,
            "redirect_uri": self.s.google_callback_url,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}", status_code=302)
        self._cookie(resp, self.state_cookie, state, PENDING_TTL)
        return resp

    async def callback(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        cookie_state = request.cookies.get(self.state_cookie, "")
        with self._lock:
            pending = self._pending.pop(state, None) if state else None
        if (pending is None or self._clock() - pending.created > PENDING_TTL
                or not secrets.compare_digest(state, cookie_state)):
            return self._fail("expired")
        if not code:
            return self._fail("cancelled")
        try:
            sub, email, verified, profile = await self._google_identity(code, pending.verifier)
        except Exception as e:
            log_event(logger, logging.WARNING, "google_exchange_failed", error=type(e).__name__)
            return self._fail("google")

        user_id = self.s.allowed_google_users.get(email.lower()) if verified else None
        if user_id is None:
            log_event(logger, logging.WARNING, "google_user_not_allowed", verified=verified)
            return self._fail("not_allowed")
        if not self._bind_identity(sub, user_id):
            log_event(logger, logging.WARNING, "google_identity_mismatch", user_id=user_id)
            return self._fail("not_allowed")

        self._store.user_doc(user_id, "account").save(profile)
        token = secrets.token_urlsafe(32)
        now = int(self._clock())
        rec = {"user_id": user_id, "created": now, "last_seen": now,
               "expires": now + self.s.app_session_ttl_seconds}
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items() if v["expires"] > now}
            self._sessions[_hash(token)] = rec
            self._sessions_doc.save(self._sessions)
        log_event(logger, logging.INFO, "app_login_ok", user_id=user_id)
        resp = RedirectResponse("/app/", status_code=302)
        resp.delete_cookie(self.state_cookie, path="/", secure=self._secure, httponly=True,
                           samesite="lax")
        self._cookie(resp, self.session_cookie, token, self.s.app_session_ttl_seconds)
        return resp

    def _bind_identity(self, sub: str, user_id: str) -> bool:
        """Pin Google ``sub`` <-> user_id on first sign-in; require a match afterwards."""
        def bind(data: dict[str, Any]) -> bool:
            subs: dict[str, str] = data.setdefault("google_sub", {})
            bound_user = subs.get(sub)
            if bound_user is not None:
                return bound_user == user_id
            if user_id in subs.values():
                return False  # this user already signed in with a different Google account
            subs[sub] = user_id
            return True
        return bool(self._identities_doc.update(bind))

    async def _google_identity(self, code: str,
                               verifier: str) -> tuple[str, str, bool, dict[str, str]]:
        client = self._http or httpx2.AsyncClient(timeout=10)
        try:
            r = await client.post(GOOGLE_TOKEN_URL, data={
                "client_id": self.s.google_client_id,
                "client_secret": self._secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": self.s.google_callback_url,
            }, headers={"Accept": "application/json"})
            r.raise_for_status()
            access = r.json().get("access_token")
            if not access:
                raise RuntimeError("no access_token from Google")
            u = await client.get(GOOGLE_USERINFO_URL,
                                 headers={"Authorization": f"Bearer {access}"})
            u.raise_for_status()
            info = u.json()
            sub, email = str(info["sub"]), str(info.get("email", ""))
            verified = info.get("email_verified") is True
            if not sub or not email:
                raise RuntimeError("incomplete userinfo")
            name = str(info.get("given_name") or info.get("name") or "")[:80]
            picture = str(info.get("picture") or "")
            if not _is_google_photo(picture):
                picture = ""
            return sub, email, verified, {"name": name, "picture": picture}
        finally:
            if self._http is None:
                await client.aclose()

    # ------------------------------------------------------------ sessions --
    def user_for(self, request: Request) -> str | None:
        if self._dev_user:
            return self._dev_user
        token = request.cookies.get(self.session_cookie)
        if not token:
            return None
        key = _hash(token)
        now = int(self._clock())
        with self._lock:
            rec = self._sessions.get(key)
            if rec is None or rec["expires"] <= now:
                return None
            if rec["user_id"] not in self._allowed_user_ids():
                return None  # removed from the allowlist: revoked immediately
            if now - rec["last_seen"] > LAST_SEEN_WRITE_INTERVAL:
                rec["last_seen"] = now
                rec["expires"] = now + self.s.app_session_ttl_seconds  # sliding
                self._sessions_doc.save(self._sessions)
            return str(rec["user_id"])

    def logout(self, request: Request, resp: Response) -> None:
        token = request.cookies.get(self.session_cookie)
        if token:
            with self._lock:
                if self._sessions.pop(_hash(token), None) is not None:
                    self._sessions_doc.save(self._sessions)
        resp.delete_cookie(self.session_cookie, path="/", secure=self._secure, httponly=True,
                           samesite="lax")

    def profile(self, user_id: str) -> dict[str, str]:
        data = self._store.user_doc(user_id, "account").load()
        return {"name": str(data.get("name", "")), "picture": str(data.get("picture", ""))}

    def revoke_user(self, user_id: str) -> None:
        with self._lock:
            self._sessions = {k: v for k, v in self._sessions.items()
                              if v["user_id"] != user_id}
            self._sessions_doc.save(self._sessions)

    # --------------------------------------------------------------- guard --
    def check_origin(self, request: Request) -> bool:
        """Mutating requests must come from our own page."""
        origin = request.headers.get("origin", "")
        if origin != self.s.public_base_url:
            return False
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        return ctype == "application/json"
