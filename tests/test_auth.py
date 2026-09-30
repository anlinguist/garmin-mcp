"""End-to-end OAuth through the real ASGI app, with GitHub mocked."""

from __future__ import annotations

import base64
import contextlib
import functools
import hashlib
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
import pytest

from garmin_mcp.auth import GitHubOAuthProvider
from garmin_mcp.config import Settings
from garmin_mcp.server import build_app
from garmin_mcp.tokens import EncryptedFileTokenStore
from tests.conftest import make_service

BASE = "https://mcp.example.com"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
ALLOWED_GH_ID = "1001"
OTHER_GH_ID = "2002"


def _github_mock(user_id: str) -> httpx2.AsyncClient:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path == "/login/oauth/access_token":
            body = parse_qs(req.content.decode())
            assert body["client_secret"] == ["gh-secret"]
            assert body["code_verifier"][0]  # GitHub-side PKCE is used
            return httpx2.Response(200, json={"access_token": "gho_x"})
        if req.url.path == "/user":
            return httpx2.Response(200, json={"id": int(user_id), "login": f"user{user_id}"})
        return httpx2.Response(404)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        public_base_url=BASE, data_dir=tmp_path, github_client_id="gh-client",
        allowed_github_users={ALLOWED_GH_ID: "andrew"},
    )


class Harness:
    def __init__(self, tmp_path: Path, fernet_key: str, provider, registry, gh_user: str) -> None:
        self.settings = _settings(tmp_path)
        store = EncryptedFileTokenStore(self.settings.oauth_state_dir, fernet_key)
        self.auth = GitHubOAuthProvider(self.settings, "gh-secret", store, http=_github_mock(gh_user))
        self.app = build_app(self.settings, make_service(provider, registry), self.auth)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url=BASE,
                                  headers={"host": "mcp.example.com"})


async def _register(c: httpx2.AsyncClient, redirect: str = CALLBACK) -> httpx2.Response:
    return await c.post("/register", json={
        "redirect_uris": [redirect], "client_name": "claude",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "client_secret_post",
    })


def _pkce() -> tuple[str, str]:
    v = secrets.token_urlsafe(48)
    c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, c


async def _authorize(c: httpx2.AsyncClient, client_id: str, challenge: str,
                     resource: str | None = BASE + "/mcp") -> httpx2.Response:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
              "code_challenge": challenge, "code_challenge_method": "S256", "state": "st8"}
    if resource:
        params["resource"] = resource
    return await c.get("/authorize", params=params)


async def _full_login(h: Harness) -> tuple[httpx2.AsyncClient, dict[str, Any], dict[str, Any]]:
    c = h.client()
    reg = (await _register(c)).json()
    verifier, challenge = _pkce()
    r = await _authorize(c, reg["client_id"], challenge)
    assert r.status_code == 302
    gh = urlparse(r.headers["location"])
    assert gh.netloc == "github.com"
    gh_state = parse_qs(gh.query)["state"][0]
    cb = await c.get("/github/callback", params={"code": "ghcode", "state": gh_state})
    assert cb.status_code == 302
    back = urlparse(cb.headers["location"])
    q = parse_qs(back.query)
    assert f"{back.scheme}://{back.netloc}{back.path}" == CALLBACK
    assert q["state"] == ["st8"] and q["iss"] == [BASE]
    return c, reg, {"code": q.get("code", [None])[0], "error": q.get("error", [None])[0],
                    "verifier": verifier}


async def _token(c, reg, code, verifier, resource=BASE + "/mcp") -> httpx2.Response:
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
            "client_id": reg["client_id"], "client_secret": reg["client_secret"],
            "code_verifier": verifier}
    if resource:
        data["resource"] = resource
    return await c.post("/token", data=data)


async def _mcp(c, token: str | None, method: str = "tools/list", params: dict | None = None):
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json",
               "mcp-protocol-version": "2025-11-25"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return await c.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def run(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        async def main():
            h: Harness = k.get("h") or k["h_other"]
            async with h.app.router.lifespan_context(h.app):
                await fn(*a, **k)
        anyio.run(main)
    return wrapper


@pytest.fixture
def h(tmp_path, fernet_key, provider, registry) -> Harness:
    return Harness(tmp_path, fernet_key, provider, registry, ALLOWED_GH_ID)


@pytest.fixture
def h_other(tmp_path, fernet_key, provider, registry) -> Harness:
    return Harness(tmp_path, fernet_key, provider, registry, OTHER_GH_ID)


# ---------------------------------------------------------------------------
@run
async def test_discovery_and_401(h: Harness) -> None:
    async with h.client() as c:
        r = await _mcp(c, None)
        assert r.status_code == 401
        assert "resource_metadata=" in r.headers["www-authenticate"]
        prm = (await c.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert prm["resource"] == BASE + "/mcp"
        assert prm["authorization_servers"][0].rstrip("/") == BASE
        asm = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert asm["registration_endpoint"].startswith(BASE)


@run
async def test_register_rejects_foreign_redirect(h: Harness) -> None:
    async with h.client() as c:
        r = await _register(c, "https://evil.example/cb")
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
        assert (await _register(c, "http://localhost:6274/oauth/callback")).status_code == 201


@run
async def test_full_flow_and_tool_call(h: Harness) -> None:
    c, reg, res = await _full_login(h)
    async with contextlib.aclosing(c):
        assert res["code"]
        tok = await _token(c, reg, res["code"], res["verifier"])
        assert tok.status_code == 200, tok.text
        at = tok.json()
        r = await _mcp(c, at["access_token"])
        assert r.status_code == 200
        names = {t["name"] for t in r.json()["result"]["tools"]}
        assert names == {"search", "execute", "recent_activities"}
        r = await _mcp(c, at["access_token"], "tools/call",
                       {"name": "execute", "arguments": {"method": "delete_activity",
                                                         "args": {"activity_id": "1"}}})
        assert r.json()["result"]["isError"] is True
        assert "writes are disabled" in r.json()["result"]["content"][0]["text"]

        # code is single use
        again = await _token(c, reg, res["code"], res["verifier"])
        assert again.status_code == 400

        # refresh rotates; the old refresh token dies
        data = {"grant_type": "refresh_token", "refresh_token": at["refresh_token"],
                "client_id": reg["client_id"], "client_secret": reg["client_secret"]}
        r1 = await c.post("/token", data=data)
        assert r1.status_code == 200
        r2 = await c.post("/token", data=data)
        assert r2.status_code == 400 and r2.json()["error"] == "invalid_grant"


@run
async def test_wrong_pkce_verifier_rejected(h: Harness) -> None:
    c, reg, res = await _full_login(h)
    async with contextlib.aclosing(c):
        r = await _token(c, reg, res["code"], "wrong-verifier-" + "x" * 40)
        assert r.status_code == 400


@run
async def test_wrong_resource_rejected_at_authorize(h: Harness) -> None:
    async with h.client() as c:
        reg = (await _register(c)).json()
        _, ch = _pkce()
        r = await _authorize(c, reg["client_id"], ch, resource="https://other.example/mcp")
        assert r.status_code == 302
        assert "invalid_target" in r.headers["location"]


@run
async def test_unlisted_github_user_denied(h_other: Harness) -> None:
    c, reg, res = await _full_login(h_other)
    async with contextlib.aclosing(c):
        assert res["code"] is None and res["error"] == "access_denied"


@run
async def test_forged_or_replayed_github_state(h: Harness) -> None:
    async with h.client() as c:
        r = await c.get("/github/callback", params={"code": "x", "state": "forged"})
        assert r.status_code == 400
        reg = (await _register(c)).json()
        _, ch = _pkce()
        r = await _authorize(c, reg["client_id"], ch)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        assert (await c.get("/github/callback", params={"code": "x", "state": state})).status_code == 302
        assert (await c.get("/github/callback", params={"code": "x", "state": state})).status_code == 400


@run
async def test_removed_user_loses_access(h: Harness) -> None:
    c, reg, res = await _full_login(h)
    async with contextlib.aclosing(c):
        at = (await _token(c, reg, res["code"], res["verifier"])).json()
        assert (await _mcp(c, at["access_token"])).status_code == 200
        h.auth.s = replace(h.settings, allowed_github_users={})
        assert (await _mcp(c, at["access_token"])).status_code == 401
        data = {"grant_type": "refresh_token", "refresh_token": at["refresh_token"],
                "client_id": reg["client_id"], "client_secret": reg["client_secret"]}
        assert (await c.post("/token", data=data)).status_code == 400


@run
async def test_bogus_bearer_rejected(h: Harness) -> None:
    async with h.client() as c:
        assert (await _mcp(c, "not-a-token")).status_code == 401


@run
async def test_wrong_host_header_rejected(h: Harness) -> None:
    c, reg, res = await _full_login(h)
    async with contextlib.aclosing(c):
        at = (await _token(c, reg, res["code"], res["verifier"])).json()
        r = await c.post("/mcp", headers={
            "host": "evil.example", "authorization": f"Bearer {at['access_token']}",
            "accept": "application/json, text/event-stream", "content-type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code in (400, 421)


@run
async def test_state_persists_encrypted(h: Harness) -> None:
    c, reg, res = await _full_login(h)
    async with contextlib.aclosing(c):
        at = (await _token(c, reg, res["code"], res["verifier"])).json()
    raw = (h.settings.oauth_state_dir / "oauth-state.fernet").read_bytes()
    assert reg["client_secret"].encode() not in raw
    assert at["refresh_token"].encode() not in raw
