"""UnofficialSession: backoff, token write-back, error translation, Cloudflare detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.providers import CloudflareBlocked, RateLimited, ReauthRequired, UpstreamError
from garmin_mcp.providers.unofficial import (
    UnofficialProvider,
    UnofficialSession,
    _instrument_session,
    _looks_like_cloudflare,
)
from garmin_mcp.tokens import EncryptedFileTokenStore


class _Client:
    def __init__(self) -> None:
        self.blob = '{"di_token": "a", "di_refresh_token": "r", "di_client_id": "c"}'

    def dumps(self) -> str:
        return self.blob


class _Garmin:
    def __init__(self) -> None:
        self.client = _Client()


def _session(tmp_path: Path, key: str) -> tuple[UnofficialSession, _Garmin, EncryptedFileTokenStore]:
    store = EncryptedFileTokenStore(tmp_path, key)
    g = _Garmin()
    store.save("andrew", g.client.blob)
    s = UnofficialSession("andrew", g, store, g.client.blob, sleep=lambda _: None)
    return s, g, store


def test_retries_429_then_succeeds(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)
    n = {"i": 0}

    def fn(g: Any) -> str:
        n["i"] += 1
        if n["i"] < 3:
            raise GarminConnectTooManyRequestsError("Rate limit exceeded")
        return "ok"

    assert s.call(fn, {}, retry=True) == "ok" and n["i"] == 3


def test_retries_5xx_but_not_4xx(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)
    n = {"i": 0}

    def fn5(g: Any) -> str:
        n["i"] += 1
        raise GarminConnectConnectionError("API Error 503")

    with pytest.raises(UpstreamError):
        s.call(fn5, {}, retry=True)
    assert n["i"] == 3

    n["i"] = 0

    def fn4(g: Any) -> str:
        n["i"] += 1
        raise GarminConnectConnectionError("API Error 400 - bad")

    with pytest.raises(UpstreamError):
        s.call(fn4, {}, retry=True)
    assert n["i"] == 1


def test_writes_are_not_retried(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)
    n = {"i": 0}

    def fn(g: Any) -> str:
        n["i"] += 1
        raise GarminConnectTooManyRequestsError("429")

    with pytest.raises(RateLimited):
        s.call(fn, {}, retry=False)
    assert n["i"] == 1


def test_auth_error_maps_to_relogin(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)

    def fn(g: Any) -> str:
        raise GarminConnectAuthenticationError("Authentication failed: API Error 401")

    with pytest.raises(ReauthRequired, match="garmin-mcp-login --user andrew"):
        s.call(fn, {}, retry=True)


def test_refreshed_tokens_written_back_encrypted(tmp_path, fernet_key) -> None:
    s, g, store = _session(tmp_path, fernet_key)
    new = '{"di_token": "b", "di_refresh_token": "r2", "di_client_id": "c"}'

    def fn(garmin: Any) -> str:
        garmin.client.blob = new  # simulate in-library refresh
        return "ok"

    s.call(fn, {}, retry=True)
    assert store.load("andrew") == new
    assert b"r2" not in (tmp_path / "andrew.fernet").read_bytes()


def test_error_messages_do_not_leak_payload(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)

    def fn(g: Any) -> str:
        raise RuntimeError('boom {"di_refresh_token": "SECRET"}')

    with pytest.raises(UpstreamError) as exc:
        s.call(fn, {}, retry=False)
    assert "SECRET" not in str(exc.value)


class _Resp:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status_code = status
        self.headers = headers


class _Sess:
    def __init__(self, resp: _Resp) -> None:
        self.resp = resp

    def request(self, method: str, url: str, **kw: Any) -> _Resp:
        return self.resp


def test_cloudflare_detection_headers() -> None:
    assert _looks_like_cloudflare(403, {"cf-mitigated": "challenge"})
    assert _looks_like_cloudflare(403, {"server": "cloudflare", "content-type": "text/html"})
    assert not _looks_like_cloudflare(403, {"server": "cloudflare", "content-type": "application/json"})
    assert not _looks_like_cloudflare(200, {"server": "cloudflare", "content-type": "text/html"})


def test_cloudflare_block_reported_distinctly(tmp_path, fernet_key) -> None:
    s, *_ = _session(tmp_path, fernet_key)
    sess = _Sess(_Resp(403, {"server": "cloudflare", "content-type": "text/html"}))
    _instrument_session(sess)
    n = {"i": 0}

    def fn(g: Any) -> str:
        n["i"] += 1
        sess.request("GET", "https://connectapi.garmin.com/x")
        raise GarminConnectConnectionError("API Error 403")

    with pytest.raises(CloudflareBlocked):
        s.call(fn, {}, retry=True)
    assert n["i"] == 1  # never retried into a challenge


def test_provider_without_tokens_asks_for_login(tmp_path, fernet_key) -> None:
    p = UnofficialProvider(EncryptedFileTokenStore(tmp_path, fernet_key))
    with pytest.raises(ReauthRequired, match="garmin-mcp-login --user andrew"):
        p.get_client("andrew")


def test_external_relogin_not_clobbered(tmp_path, fernet_key) -> None:
    s, g, store = _session(tmp_path, fernet_key)
    relogin = '{"di_token": "fresh", "di_refresh_token": "fresh-r", "di_client_id": "c"}'
    store.save("andrew", relogin)  # garmin-mcp-login ran while server was up

    def fn(garmin: Any) -> str:
        garmin.client.blob = '{"di_token": "stale-refresh", "di_refresh_token": "x", "di_client_id": "c"}'
        return "ok"

    s.call(fn, {}, retry=True)
    assert store.load("andrew") == relogin
