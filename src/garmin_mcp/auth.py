"""MCP OAuth 2.1 authorization server that delegates user login to GitHub.

Flow (claude.ai as the MCP client):
  1. Client registers via DCR (/register). Only allowlisted redirect URIs are accepted.
  2. /authorize (SDK validates client, redirect_uri, PKCE params) -> ``authorize()``
     checks the RFC 8707 ``resource``, stores the request under a fresh random
     state and redirects the browser to GitHub (with its own PKCE).
  3. /github/callback exchanges GitHub's code, reads the numeric user id, checks
     it against the allowlist, mints a single-use MCP auth code and redirects
     back to the client's registered redirect_uri.
  4. /token (SDK verifies PKCE + redirect_uri + client) -> ``exchange_authorization_code``
     issues an opaque access token (audience = this server's /mcp URL) and a
     rotating refresh token.

GitHub tokens are used once to read the user id and then discarded; they
never reach the MCP client (no token passthrough).

Persistence: registered clients and refresh-token hashes are kept in a
Fernet-encrypted state file so claude.ai stays connected across restarts.
Access tokens and pending authorizations are in memory only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx2
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .config import Settings
from .logs import log_event
from .tokens import EncryptedFileTokenStore

logger = logging.getLogger(__name__)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

SCOPE = "garmin:read"
PENDING_TTL = 600
CODE_TTL = 300
MAX_PENDING = 100
MAX_CLIENTS = 200
MAX_ACCESS_TOKENS = 1000


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def _is_loopback_http(uri: str) -> bool:
    p = urlparse(uri)
    return p.scheme == "http" and p.hostname in {"localhost", "127.0.0.1", "::1"}


@dataclass
class _Pending:
    client_id: str
    params: AuthorizationParams
    github_verifier: str
    created: float


class GitHubOAuthProvider:
    """Implements mcp.server.auth.provider.OAuthAuthorizationServerProvider."""

    def __init__(self, settings: Settings, github_client_secret: str,
                 state_store: EncryptedFileTokenStore,
                 http: httpx2.AsyncClient | None = None) -> None:
        self.s = settings
        self._gh_secret = github_client_secret
        self._store = state_store
        self._http = http
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._codes: dict[str, tuple[AuthorizationCode, str]] = {}  # code -> (code, user_id)
        self._access: dict[str, AccessToken] = {}  # sha256(token) -> AccessToken
        self._clients: dict[str, dict[str, Any]] = {}
        self._refresh: dict[str, dict[str, Any]] = {}  # sha256(token) -> record
        self._load_state()

    # ------------------------------------------------------------ persistence
    def _load_state(self) -> None:
        raw = self._store.load("oauth-state")
        if not raw:
            return
        data = json.loads(raw)
        self._clients = data.get("clients", {})
        self._refresh = data.get("refresh", {})

    def _save_state(self) -> None:
        now = time.time()
        self._refresh = {
            k: v for k, v in self._refresh.items() if v["expires_at"] > now
        }
        self._store.save(
            "oauth-state", json.dumps({"clients": self._clients, "refresh": self._refresh})
        )

    # ---------------------------------------------------------- allowlisting
    def user_for_subject(self, subject: str | None) -> str | None:
        """Live allowlist check: removing a user from config revokes access."""
        if not subject or not subject.startswith("github:"):
            return None
        return self.s.allowed_github_users.get(subject.removeprefix("github:"))

    def _redirect_allowed(self, uri: str) -> bool:
        if uri in self.s.oauth_allowed_redirect_uris:
            return True
        return self.s.oauth_allow_loopback_redirects and _is_loopback_http(uri)

    # ------------------------------------------------------------- clients --
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._clients.get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in (client_info.redirect_uris or [])]
        bad = [u for u in uris if not self._redirect_allowed(u)]
        if not uris or bad:
            log_event(logger, logging.WARNING, "oauth_register_rejected", redirect_uris=uris)
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="redirect_uri not permitted by this server",
            )
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                # Registration is open, so never evict a client that holds a live
                # refresh token (that would let anyone disconnect claude.ai).
                in_use = {r["client_id"] for r in self._refresh.values()}
                idle = [c for c in self._clients if c not in in_use]
                if not idle:
                    raise RegistrationError(error="invalid_client_metadata",
                                            error_description="registration limit reached")
                oldest = min(idle, key=lambda c: self._clients[c].get("client_id_issued_at") or 0)
                self._clients.pop(oldest)
            self._clients[client_info.client_id] = client_info.model_dump(mode="json")
            self._save_state()
        log_event(logger, logging.INFO, "oauth_client_registered",
                  client_id=client_info.client_id, redirect_uris=uris)

    # ----------------------------------------------------------- authorize --
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        resource = (params.resource or "").rstrip("/")
        if resource and resource != self.s.mcp_url:
            raise AuthorizeError(error="invalid_target",
                                 error_description="resource must be this server's MCP URL")
        if params.scopes and set(params.scopes) - {SCOPE}:
            raise AuthorizeError(error="invalid_scope", error_description="unsupported scope")
        state = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        now = time.time()
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if now - v.created < PENDING_TTL}
            if len(self._pending) >= MAX_PENDING:
                raise AuthorizeError(error="temporarily_unavailable",
                                     error_description="too many pending logins")
            self._pending[state] = _Pending(client.client_id, params, verifier, now)
        query = urlencode({
            "client_id": self.s.github_client_id,
            "redirect_uri": self.s.github_callback_url,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "allow_signup": "false",
        })
        return f"{GITHUB_AUTHORIZE_URL}?{query}"

    async def github_callback(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        with self._lock:
            pending = self._pending.pop(state, None) if state else None
        if pending is None or time.time() - pending.created > PENDING_TTL:
            return HTMLResponse("Login session expired or invalid. Start again from Claude.",
                                status_code=400)
        p = pending.params
        redirect_uri = str(p.redirect_uri)

        def back(**q: str | None) -> RedirectResponse:
            return RedirectResponse(
                construct_redirect_uri(redirect_uri, state=p.state, iss=self.s.issuer_url, **q),
                status_code=302,
            )

        if not code:
            return back(error="access_denied", error_description="GitHub login cancelled")
        try:
            gh_id, gh_login = await self._github_identity(code, pending.github_verifier)
        except Exception as e:
            log_event(logger, logging.WARNING, "github_exchange_failed", error=type(e).__name__)
            return back(error="server_error", error_description="GitHub login failed")

        user_id = self.s.allowed_github_users.get(gh_id)
        if user_id is None:
            log_event(logger, logging.WARNING, "github_user_not_allowed",
                      github_id=gh_id, github_login=gh_login)
            return back(error="access_denied", error_description="user not allowed")

        mcp_code = secrets.token_urlsafe(32)
        auth_code = AuthorizationCode(
            code=mcp_code,
            scopes=p.scopes or [SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=pending.client_id,
            code_challenge=p.code_challenge,
            redirect_uri=p.redirect_uri,
            redirect_uri_provided_explicitly=p.redirect_uri_provided_explicitly,
            resource=self.s.mcp_url,
            subject=f"github:{gh_id}",
        )
        with self._lock:
            now = time.time()
            self._codes = {k: v for k, v in self._codes.items() if v[0].expires_at > now}
            self._codes[mcp_code] = (auth_code, user_id)
        log_event(logger, logging.INFO, "github_login_ok", github_login=gh_login, user_id=user_id)
        return back(code=mcp_code)

    async def _github_identity(self, code: str, verifier: str) -> tuple[str, str]:
        client = self._http or httpx2.AsyncClient(timeout=10)
        try:
            r = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "client_id": self.s.github_client_id,
                    "client_secret": self._gh_secret,
                    "code": code,
                    "redirect_uri": self.s.github_callback_url,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            gh_token = r.json().get("access_token")
            if not gh_token:
                raise RuntimeError("no access_token from GitHub")
            u = await client.get(
                GITHUB_USER_URL,
                headers={"Authorization": f"Bearer {gh_token}",
                         "Accept": "application/vnd.github+json"},
            )
            u.raise_for_status()
            info = u.json()
            return str(int(info["id"])), str(info.get("login", ""))
        finally:
            if self._http is None:
                await client.aclose()

    # ---------------------------------------------------------------- codes --
    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        with self._lock:
            item = self._codes.get(authorization_code)
        if item is None:
            return None
        code, _ = item
        if code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        with self._lock:
            item = self._codes.pop(authorization_code.code, None)  # single use
        if item is None:
            raise TokenError(error="invalid_grant", error_description="code already used")
        code, user_id = item
        if self.user_for_subject(code.subject) != user_id:
            raise TokenError(error="invalid_grant", error_description="user no longer allowed")
        return self._issue(client.client_id, code.scopes, code.subject or "", user_id)

    # --------------------------------------------------------------- tokens --
    def _issue(self, client_id: str, scopes: list[str], subject: str, user_id: str) -> OAuthToken:
        now = int(time.time())
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(48)
        at = AccessToken(
            token=access, client_id=client_id, scopes=scopes,
            expires_at=now + self.s.access_token_ttl_seconds,
            resource=self.s.mcp_url, subject=subject,
            claims={"iss": self.s.issuer_url, "user_id": user_id},
        )
        with self._lock:
            self._access = {k: v for k, v in self._access.items() if (v.expires_at or 0) > now}
            if len(self._access) >= MAX_ACCESS_TOKENS:
                self._access.pop(next(iter(self._access)))
            self._access[_hash(access)] = at
            self._refresh[_hash(refresh)] = {
                "client_id": client_id, "scopes": scopes, "subject": subject,
                "expires_at": now + self.s.refresh_token_ttl_seconds,
            }
            self._save_state()
        return OAuthToken(
            access_token=access, token_type="Bearer",
            expires_in=self.s.access_token_ttl_seconds,
            scope=" ".join(scopes), refresh_token=refresh,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        rec = self._refresh.get(_hash(refresh_token))
        if not rec or rec["client_id"] != client.client_id or rec["expires_at"] < time.time():
            return None
        if self.user_for_subject(rec["subject"]) is None:
            return None
        return RefreshToken(
            token=refresh_token, client_id=rec["client_id"], scopes=rec["scopes"],
            expires_at=int(rec["expires_at"]), resource=self.s.mcp_url, subject=rec["subject"],
        )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        with self._lock:
            rec = self._refresh.pop(_hash(refresh_token.token), None)  # rotate
        if rec is None:
            raise TokenError(error="invalid_grant", error_description="refresh token reused")
        user_id = self.user_for_subject(rec["subject"])
        if user_id is None:
            with self._lock:
                self._save_state()
            raise TokenError(error="invalid_grant", error_description="user no longer allowed")
        return self._issue(client.client_id, scopes or rec["scopes"], rec["subject"], user_id)

    async def load_access_token(self, token: str) -> AccessToken | None:
        at = self._access.get(_hash(token))
        if at is None or (at.expires_at and at.expires_at < time.time()):
            return None
        # Re-check the allowlist on every request.
        user_id = self.user_for_subject(at.subject)
        if user_id is None or (at.claims or {}).get("user_id") != user_id:
            return None
        return at

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self._lock:
            self._access.pop(_hash(token.token), None)
            if self._refresh.pop(_hash(token.token), None) is not None:
                self._save_state()
