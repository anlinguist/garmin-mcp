"""Rabbit web app: Google sign-in, sessions, request guards and Web Push.

Drives the real ASGI app (MCP server + /app routes) with Google and the push
service mocked by MockTransport.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import anyio
import http_ece
import httpx2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from garmin_mcp.appstore import AppStore
from garmin_mcp.config import Settings, _parse_email_map
from garmin_mcp.garminlogin import FakeLoginProvider, LoginManager
from garmin_mcp.server import build_app
from garmin_mcp.tokens import EncryptedFileTokenStore
from garmin_mcp.webapp import build_app_routes
from garmin_mcp.webauth import WebAuth
from garmin_mcp.webpush import WebPush, endpoint_allowed, generate_vapid_key, parse_subscription
from tests.conftest import make_service

BASE = "https://rabbit.example.com"
HOST = "rabbit.example.com"
JSON_HDRS = {"origin": BASE, "content-type": "application/json"}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Google:
    """Mock Google token + userinfo endpoints; identity is settable per test."""

    def __init__(self) -> None:
        self.sub, self.email, self.verified = "g-111", "Friend@Example.com", True
        self.picture = "https://lh3.googleusercontent.com/a/abc=s96-c"
        self.token_requests: list[dict[str, list[str]]] = []

    def client(self) -> httpx2.AsyncClient:
        def handler(req: httpx2.Request) -> httpx2.Response:
            if req.url.host == "oauth2.googleapis.com":
                self.token_requests.append(parse_qs(req.content.decode()))
                return httpx2.Response(200, json={"access_token": "ya29.x"})
            if req.url.host == "openidconnect.googleapis.com":
                assert req.headers["authorization"] == "Bearer ya29.x"
                return httpx2.Response(200, json={
                    "sub": self.sub, "email": self.email, "email_verified": self.verified,
                    "given_name": "Friend", "picture": self.picture})
            return httpx2.Response(404)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class PushService:
    """Mock push endpoint that records requests and returns a settable status."""

    def __init__(self) -> None:
        self.status = 201
        self.requests: list[httpx2.Request] = []

    def client(self) -> httpx2.Client:
        def handler(req: httpx2.Request) -> httpx2.Response:
            self.requests.append(req)
            return httpx2.Response(self.status)
        return httpx2.Client(transport=httpx2.MockTransport(handler))


class Harness:
    def __init__(self, tmp_path: Path, fernet_key: str, provider: Any, registry: Any) -> None:
        self.tmp = tmp_path
        self.key = fernet_key
        self.settings = Settings(
            public_base_url=BASE, data_dir=tmp_path, app_enabled=True,
            google_client_id="g-client",
            allowed_google_users=_parse_email_map(
                ["friend@example.com:friend", "andrew@example.com:andrew"]),
            vapid_subject="mailto:ops@example.com",
        )
        self.google = Google()
        self.pushsvc = PushService()
        self.vapid_key = generate_vapid_key()
        self.service = make_service(provider, registry)
        self.rebuild()

    def rebuild(self, settings: Settings | None = None) -> None:
        """Fresh WebAuth/WebPush over the same data dir (simulates a restart)."""
        if settings is not None:
            self.settings = settings
        store = AppStore(self.settings.app_dir, self.key)
        self.auth = WebAuth(self.settings, store, "g-secret", http=self.google.client())
        self.push = WebPush(store, self.vapid_key, self.settings.vapid_subject,
                            http=self.pushsvc.client())
        self.tokens = tokens = EncryptedFileTokenStore(self.settings.token_dir, self.key)
        self.logins = LoginManager(FakeLoginProvider(tokens, delay=0), store,
                                   run=lambda fn: fn())
        routes = build_app_routes(self.settings, self.auth, tokens, self.push, self.logins,
                                  fake_garmin=True)
        self.app = build_app(self.settings, self.service, None, None, routes)

    def client(self, host: str = HOST) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url=BASE,
                                  headers={"host": host})


def run(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        async def main():
            await fn(*a, **k)
        anyio.run(main)
    return wrapper


@pytest.fixture
def h(tmp_path, fernet_key, provider, registry) -> Harness:
    return Harness(tmp_path, fernet_key, provider, registry)


async def sign_in(h: Harness, c: httpx2.AsyncClient, *, tamper_state: bool = False) -> httpx2.Response:
    r = await c.get("/app/auth/google/start")
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.netloc == "accounts.google.com"
    q = parse_qs(loc.query)
    assert q["scope"] == ["openid email profile"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [BASE + "/app/auth/google/callback"]
    state = q["state"][0]
    if tamper_state:
        c.cookies.set(h.auth.state_cookie, "someone-elses-state", domain=HOST)
    return await c.get("/app/auth/google/callback", params={"state": state, "code": "abc"})


# ----------------------------------------------------------------- sign-in --
@run
async def test_sign_in_sets_session_and_me_works(h: Harness) -> None:
    async with h.client() as c:
        assert (await c.get("/app/api/me")).status_code == 401
        r = await sign_in(h, c)
        assert r.status_code == 302 and r.headers["location"] == "/app/"
        set_cookie = r.headers.get_list("set-cookie")
        session = [s for s in set_cookie if s.startswith("__Host-rabbit_session=")][0]
        for attr in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            assert attr.lower() in session.lower()
        assert h.google.token_requests[0]["code_verifier"][0]
        me = (await c.get("/app/api/me")).json()
        assert me["user_id"] == "friend"
        assert me["profile"] == {"name": "Friend",
                                 "picture": "https://lh3.googleusercontent.com/a/abc=s96-c"}
        assert me["garmin_connected"] is False
        assert me["push"]["available"] is True and me["push"]["public_key"]


@run
async def test_session_survives_restart_and_only_hash_is_stored(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        token = c.cookies.get(h.auth.session_cookie)
    raw = (h.settings.app_dir / "sessions.fernet").read_bytes()
    assert token.encode() not in raw
    h.rebuild()
    async with h.client() as c:
        c.cookies.set(h.auth.session_cookie, token, domain=HOST)
        assert (await c.get("/app/api/me")).json()["user_id"] == "friend"


@run
async def test_state_cookie_must_match(h: Harness) -> None:
    async with h.client() as c:
        r = await sign_in(h, c, tamper_state=True)
        assert r.headers["location"] == "/app/?auth_error=expired"
        assert (await c.get("/app/api/me")).status_code == 401
        assert h.google.token_requests == []  # never exchanged the code


@run
async def test_callback_state_is_single_use(h: Harness) -> None:
    async with h.client() as c:
        r = await c.get("/app/auth/google/start")
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        ok = await c.get("/app/auth/google/callback", params={"state": state, "code": "a"})
        assert ok.headers["location"] == "/app/"
        c.cookies.set(h.auth.state_cookie, state, domain=HOST)
        again = await c.get("/app/auth/google/callback", params={"state": state, "code": "a"})
        assert again.headers["location"] == "/app/?auth_error=expired"


@pytest.mark.parametrize("email,verified", [
    ("stranger@example.com", True),
    ("friend@example.com", False),
])
def test_rejects_unlisted_or_unverified(h: Harness, email: str, verified: bool) -> None:
    @run
    async def go() -> None:
        h.google.email, h.google.verified = email, verified
        async with h.client() as c:
            r = await sign_in(h, c)
            assert r.headers["location"] == "/app/?auth_error=not_allowed"
            assert (await c.get("/app/api/me")).status_code == 401
    go()


@run
async def test_google_sub_is_pinned(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
    h.google.sub = "g-999"  # same email, different Google account (email reassigned)
    async with h.client() as c:
        r = await sign_in(h, c)
        assert r.headers["location"] == "/app/?auth_error=not_allowed"


@run
async def test_allowlist_removal_revokes_existing_session(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        token = c.cookies.get(h.auth.session_cookie)
    h.rebuild(replace(h.settings, allowed_google_users={"andrew@example.com": "andrew"}))
    async with h.client() as c:
        c.cookies.set(h.auth.session_cookie, token, domain=HOST)
        assert (await c.get("/app/api/me")).status_code == 401


@run
async def test_logout_revokes(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        token = c.cookies.get(h.auth.session_cookie)
        r = await c.post("/app/auth/logout", headers=JSON_HDRS, content=b"{}")
        assert r.status_code == 200
    async with h.client() as c:
        c.cookies.set(h.auth.session_cookie, token, domain=HOST)
        assert (await c.get("/app/api/me")).status_code == 401


# ------------------------------------------------------------------ guards --
@run
async def test_mutating_calls_need_origin_and_json(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        url = "/app/api/push/test"
        assert (await c.post(url, content=b"{}")).status_code == 403
        assert (await c.post(url, content=b"{}", headers={
            "origin": "https://evil.example", "content-type": "application/json"})).status_code == 403
        assert (await c.post(url, content=b"x=1", headers={
            "origin": BASE, "content-type": "application/x-www-form-urlencoded"})).status_code == 403
        assert (await c.post(url, content=b"{}", headers=JSON_HDRS)).status_code == 200


@run
async def test_wrong_host_rejected(h: Harness) -> None:
    async with h.client(host="evil.example") as c:
        assert (await c.get("/app/api/me")).status_code == 400
        assert (await c.get("/app/auth/google/start")).status_code == 400


@run
async def test_oversized_body_rejected(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        r = await c.post("/app/api/push/subscribe", headers=JSON_HDRS,
                         content=json.dumps({"endpoint": "x" * 20000}).encode())
        assert r.status_code == 400


# -------------------------------------------------------------------- push --
def _browser_subscription(endpoint: str) -> tuple[dict[str, Any], ec.EllipticCurvePrivateKey, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    return {"endpoint": endpoint, "keys": {"p256dh": _b64(pub), "auth": _b64(auth)}}, key, auth


@pytest.mark.parametrize("endpoint,ok", [
    ("https://web.push.apple.com/QGuQyavXut", True),
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://wns2-bl2p.notify.windows.com/w/?token=x", True),
    ("http://web.push.apple.com/x", False),
    ("https://web.push.apple.com:8443/x", False),
    ("https://user:pw@web.push.apple.com/x", False),
    ("https://169.254.169.254/computeMetadata/v1/", False),
    ("https://metadata.google.internal/x", False),
    ("https://evilweb.push.apple.com.attacker.net/x", False),
    ("https://localhost/x", False),
])
def test_endpoint_allowlist(endpoint: str, ok: bool) -> None:
    assert endpoint_allowed(endpoint) is ok


def test_parse_subscription_rejects_bad_keys() -> None:
    good, _, _ = _browser_subscription("https://web.push.apple.com/x")
    assert parse_subscription(good) is not None
    bad = json.loads(json.dumps(good))
    bad["keys"]["p256dh"] = _b64(b"\x04" + b"\x00" * 64)  # not a curve point
    assert parse_subscription(bad) is None
    bad = json.loads(json.dumps(good))
    bad["keys"]["auth"] = _b64(b"short")
    assert parse_subscription(bad) is None
    assert parse_subscription({"endpoint": "https://web.push.apple.com/x"}) is None


@run
async def test_push_subscribe_send_decrypts_and_prunes_gone(h: Harness) -> None:
    sub, key, auth = _browser_subscription("https://web.push.apple.com/abc")
    async with h.client() as c:
        await sign_in(h, c)
        evil = dict(sub, endpoint="https://169.254.169.254/latest")
        assert (await c.post("/app/api/push/subscribe", headers=JSON_HDRS,
                             content=json.dumps(evil).encode())).status_code == 400
        r = await c.post("/app/api/push/subscribe", headers=JSON_HDRS,
                         content=json.dumps(sub).encode())
        assert r.json() == {"ok": True, "subscriptions": 1}

        r = await c.post("/app/api/push/test", headers=JSON_HDRS, content=b"{}")
        assert r.json()["results"] == [{"host": "web.push.apple.com", "status": 201,
                                        "removed": False}]
        req = h.pushsvc.requests[-1]
        assert req.headers["content-encoding"] == "aes128gcm"
        assert req.headers["authorization"].startswith("vapid t=")
        assert f"k={h.push.public_key}" in req.headers["authorization"]
        body = http_ece.decrypt(req.content, private_key=key, auth_secret=auth,
                                version="aes128gcm")
        assert json.loads(body) == {"title": "Rabbit", "body": "Notifications are working.",
                                    "url": "/app/"}

        h.pushsvc.status = 410  # subscription expired at the push service
        r = await c.post("/app/api/push/test", headers=JSON_HDRS, content=b"{}")
        assert r.json()["results"][0]["removed"] is True
        assert (await c.get("/app/api/me")).json()["push"]["subscriptions"] == 0


@run
async def test_push_subscriptions_are_per_user(h: Harness) -> None:
    sub, _, _ = _browser_subscription("https://web.push.apple.com/friend-device")
    async with h.client() as c:
        await sign_in(h, c)
        await c.post("/app/api/push/subscribe", headers=JSON_HDRS, content=json.dumps(sub).encode())
    h.google.sub, h.google.email = "g-222", "andrew@example.com"
    async with h.client() as c:
        await sign_in(h, c)
        me = (await c.get("/app/api/me")).json()
        assert me["user_id"] == "andrew" and me["push"]["subscriptions"] == 0
        await c.post("/app/api/push/test", headers=JSON_HDRS, content=b"{}")
    assert h.pushsvc.requests == []  # andrew's test push never reached the friend's device


def test_email_map_parsing() -> None:
    assert _parse_email_map(["A.B@Example.com:friend"]) == {"a.b@example.com": "friend"}
    with pytest.raises(RuntimeError):
        _parse_email_map(["no-at-sign:friend"])
    with pytest.raises(ValueError):
        _parse_email_map(["x@example.com:../etc"])


def test_broken_app_config_keeps_mcp_up(tmp_path, monkeypatch, fernet_key) -> None:
    """APP_ENABLED with missing Google config disables /app but the MCP server still starts."""
    from garmin_mcp import server

    for k, v in {"PUBLIC_BASE_URL": "http://localhost:8000", "DATA_DIR": str(tmp_path),
                 "GARMIN_MCP_FERNET_KEY": fernet_key, "APP_ENABLED": "true",
                 "GITHUB_CLIENT_ID": "gh", "ALLOWED_GITHUB_USERS": "1:andrew",
                 "GITHUB_CLIENT_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    _, app = server.create_app()
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/mcp" in paths and "/github/callback" in paths
    assert not any(str(p).startswith("/app") for p in paths if p)


def test_app_only_process_has_no_mcp_endpoint(tmp_path, monkeypatch, fernet_key) -> None:
    """MCP_ENABLED=false (the separate Rabbit service) never mounts /mcp or the OAuth AS."""
    from garmin_mcp import server

    for k, v in {"PUBLIC_BASE_URL": "http://localhost:8000", "DATA_DIR": str(tmp_path),
                 "GARMIN_MCP_FERNET_KEY": fernet_key, "MCP_ENABLED": "false",
                 "APP_ENABLED": "true", "DEV_NO_AUTH_USER": "friend"}.items():
        monkeypatch.setenv(k, v)
    _, app = server.create_app()
    paths = {str(getattr(r, "path", "")) for r in app.router.routes}
    assert "/healthz" in paths and "/app/api/me" in paths
    assert not any(p.startswith(("/mcp", "/authorize", "/token", "/register", "/.well-known",
                                 "/github")) for p in paths)

    @run
    async def go() -> None:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://localhost:8000") as c:
            assert (await c.post("/mcp", json={})).status_code == 404
            assert (await c.get("/app/api/me")).json()["user_id"] == "friend"
    go()


def test_app_only_requires_app_enabled(tmp_path, monkeypatch, fernet_key) -> None:
    from garmin_mcp import server

    for k, v in {"PUBLIC_BASE_URL": "http://localhost:8000", "DATA_DIR": str(tmp_path),
                 "GARMIN_MCP_FERNET_KEY": fernet_key, "MCP_ENABLED": "false"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="APP_ENABLED"):
        server.create_app()


@run
async def test_profile_photo_only_from_google_cdn(h: Harness) -> None:
    h.google.picture = "https://evil.example/track.png"
    async with h.client() as c:
        await sign_in(h, c)
        assert (await c.get("/app/api/me")).json()["profile"]["picture"] == ""


# ----------------------------------------------------------- Connect Garmin --
SECRET_PW = "correct-horse-battery-staple"


async def _login(c: httpx2.AsyncClient, email: str, password: str = SECRET_PW) -> httpx2.Response:
    return await c.post("/app/api/garmin/login", headers=JSON_HDRS,
                        content=json.dumps({"email": email, "password": password}).encode())


@run
async def test_connect_garmin_happy_path(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        me = (await c.get("/app/api/me")).json()
        assert me["garmin"]["connected"] is False and me["garmin"]["fake"] is True
        r = await _login(c, "ok@example.com")
        assert r.status_code == 202
        job = r.json()
        status = (await c.get(f"/app/api/garmin/login/{job['job_id']}")).json()
        assert status["state"] == "ok"
        assert (await c.get("/app/api/me")).json()["garmin"]["connected"] is True
        assert (await c.post("/app/api/garmin/disconnect", headers=JSON_HDRS,
                             content=b"{}")).status_code == 200
        assert (await c.get("/app/api/me")).json()["garmin"]["connected"] is False


@run
async def test_connect_garmin_mfa_via_api(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        job = (await _login(c, "mfa@example.com")).json()
        assert job["state"] == "needs_mfa"
        bad = await c.post("/app/api/garmin/mfa", headers=JSON_HDRS,
                           content=json.dumps({"job_id": job["job_id"], "code": "12ab"}).encode())
        assert bad.status_code == 400  # digits only
        ok = await c.post("/app/api/garmin/mfa", headers=JSON_HDRS,
                          content=json.dumps({"job_id": job["job_id"], "code": "123456"}).encode())
        assert ok.json()["state"] == "ok"


@run
async def test_connect_garmin_cooldown_response(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        job = (await _login(c, "wrong@example.com")).json()
        status = (await c.get(f"/app/api/garmin/login/{job['job_id']}")).json()
        assert status["error"] == "bad_credentials" and status["cooldown_scope"] == "user"
        r = await _login(c, "ok@example.com")
        assert r.status_code == 429 and r.json()["state"] == "cooldown"
        assert (await c.get("/app/api/me")).json()["garmin"]["cooldown_until"] is not None


@run
async def test_connect_garmin_guards(h: Harness) -> None:
    async with h.client() as c:
        await sign_in(h, c)
        job = (await _login(c, "mfa@example.com")).json()
        no_origin = await c.post("/app/api/garmin/login", content=json.dumps(
            {"email": "ok@example.com", "password": "x"}).encode(),
            headers={"content-type": "application/json"})
        assert no_origin.status_code == 403
        assert (await c.post("/app/api/garmin/login", headers=JSON_HDRS, content=json.dumps(
            {"email": "ok@example.com", "password": "x" * 300}).encode())).status_code == 400
    h.google.sub, h.google.email = "g-222", "andrew@example.com"
    async with h.client() as c:
        await sign_in(h, c)  # a different user can't see or finish friend's login
        assert (await c.get(f"/app/api/garmin/login/{job['job_id']}")).status_code == 404
        r = await c.post("/app/api/garmin/mfa", headers=JSON_HDRS, content=json.dumps(
            {"job_id": job["job_id"], "code": "123456"}).encode())
        assert r.status_code == 404
    assert h.tokens.load("andrew") is None and h.tokens.load("friend") is None


def test_password_never_reaches_logs_or_responses(h: Harness, caplog) -> None:
    @run
    async def go() -> list[str]:
        bodies = []
        async with h.client() as c:
            await sign_in(h, c)
            for email in ("wrong@example.com", "429@example.com"):
                r = await _login(c, email)
                bodies.append(r.text)
                if r.status_code == 202:
                    bodies.append((await c.get(f"/app/api/garmin/login/{r.json()['job_id']}")).text)
            bodies.append((await c.get("/app/api/me")).text)
        assert not any(SECRET_PW in b for b in bodies)
    with caplog.at_level(logging.DEBUG):
        go()
    assert SECRET_PW not in caplog.text


def test_index_has_absolute_link_preview_image(tmp_path, monkeypatch) -> None:
    import asyncio

    import httpx2
    from starlette.applications import Starlette

    from garmin_mcp import webapp
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<meta property="og:image" content="__RABBIT_ORIGIN__/app/og-image.png" />')
    (dist / "og-image.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(webapp, "WEB_DIST", dist)
    from garmin_mcp.appstore import AppStore
    from garmin_mcp.config import Settings
    from garmin_mcp.tokens import generate_key
    from garmin_mcp.webauth import WebAuth
    settings = Settings(public_base_url="https://rabbit.example.com", data_dir=tmp_path,
                        app_enabled=True, mcp_enabled=False)
    auth = WebAuth(settings, AppStore(tmp_path / "app", generate_key()), "", dev_user="friend")
    app = Starlette(routes=webapp.build_app_routes(settings, auth, None, None, None, None))

    async def go() -> None:
        t = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=t, base_url="https://rabbit.example.com") as c:
            for path in ("/app/", "/app/index.html"):
                r = await c.get(path)
                assert 'content="https://rabbit.example.com/app/og-image.png"' in r.text
                assert "__RABBIT_ORIGIN__" not in r.text and "content-security-policy" in r.headers
            assert (await c.get("/app/og-image.png")).status_code == 200
    asyncio.run(go())
