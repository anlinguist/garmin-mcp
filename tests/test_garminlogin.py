"""Rabbit's Connect Garmin flow: single-attempt logins, escalating cooldowns, MFA."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest

from garmin_mcp.appstore import AppStore
from garmin_mcp.garminlogin import (
    GLOBAL_BASE,
    GLOBAL_CAP,
    USER_LADDER,
    Busy,
    Cooldown,
    FakeLoginProvider,
    LoginManager,
)
from garmin_mcp.providers import RateLimited
from garmin_mcp.tokens import EncryptedFileTokenStore

PASSWORD = "hunter2-very-secret"


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class Deferred:
    """Runs submitted work only when told to (simulates an in-flight login)."""

    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def __call__(self, fn: Callable[[], None]) -> None:
        self.pending.append(fn)

    def drain(self) -> None:
        while self.pending:
            self.pending.pop(0)()


@pytest.fixture
def env(tmp_path, fernet_key):
    tokens = EncryptedFileTokenStore(tmp_path / "tokens", fernet_key)
    store = AppStore(tmp_path / "app", fernet_key)
    clock = Clock()
    changed: list[str] = []

    def make(run: Any = None, provider: Any = None) -> LoginManager:
        return LoginManager(provider or FakeLoginProvider(tokens, delay=0), store, clock=clock,
                            run=run or (lambda fn: fn()), on_tokens_changed=changed.append)
    return {"tokens": tokens, "store": store, "clock": clock, "make": make, "changed": changed}


def test_success_saves_tokens_and_fires_hook(env) -> None:
    lm = env["make"]()
    job = lm.start("friend", "ok@x.com", PASSWORD)
    assert job.state == "ok" and job.error is None
    assert env["tokens"].load("friend") is not None
    assert env["changed"] == ["friend"]
    assert lm.cooldown("friend") is None


def test_mfa_flow_with_retries(env) -> None:
    lm = env["make"]()
    job = lm.start("friend", "mfa@x.com", PASSWORD)
    assert job.public() == {"job_id": job.id, "state": "needs_mfa", "error": None,
                            "mfa_tries_left": 3}
    job = lm.submit_mfa("friend", job.id, "000000")
    assert (job.state, job.error, job.mfa_tries_left) == ("needs_mfa", "bad_mfa", 2)
    job = lm.submit_mfa("friend", job.id, "123456")
    assert job.state == "ok"
    assert env["tokens"].load("friend") is not None


def test_mfa_exhausted_fails_and_starts_cooldown(env) -> None:
    lm = env["make"]()
    job = lm.start("friend", "mfa@x.com", PASSWORD)
    for _ in range(3):
        job = lm.submit_mfa("friend", job.id, "000000")
    assert (job.state, job.error) == ("error", "bad_credentials")
    assert lm.cooldown("friend") == (env["clock"].t + USER_LADDER[0], "user")


def test_wrong_password_ladder_escalates_and_resets(env) -> None:
    lm, clock = env["make"](), env["clock"]
    for expected in (*USER_LADDER, USER_LADDER[-1]):
        job = lm.start("friend", "wrong@x.com", PASSWORD)
        assert job.error == "bad_credentials"
        until, scope = lm.cooldown("friend")
        assert scope == "user" and until - clock.t == expected
        with pytest.raises(Cooldown):
            lm.start("friend", "ok@x.com", PASSWORD)
        clock.t = until + 1
    assert lm.cooldown("other") is None  # per-user, not global

    clock.t += 25 * 3600  # a quiet day resets the ladder
    lm.start("friend", "wrong@x.com", PASSWORD)
    assert lm.cooldown("friend")[0] - clock.t == USER_LADDER[0]


def test_success_clears_the_ladder(env) -> None:
    lm, clock = env["make"](), env["clock"]
    lm.start("friend", "wrong@x.com", PASSWORD)
    clock.t += USER_LADDER[0] + 1
    lm.start("friend", "ok@x.com", PASSWORD)
    lm.start("friend", "wrong@x.com", PASSWORD)
    assert lm.cooldown("friend")[0] - clock.t == USER_LADDER[0]


@pytest.mark.parametrize("email", ["429@x.com", "cf@x.com"])
def test_rate_limit_is_global_and_doubles_to_cap(env, email: str) -> None:
    lm, clock = env["make"](), env["clock"]
    delays = []
    for _ in range(7):
        job = lm.start("friend", email, PASSWORD)
        assert job.error in ("rate_limited", "blocked")
        until, scope = lm.cooldown("someone-else")  # blocks every user
        assert scope == "global"
        delays.append(until - clock.t)
        clock.t = until + 1
    assert delays[:3] == [GLOBAL_BASE, 2 * GLOBAL_BASE, 4 * GLOBAL_BASE]
    assert max(delays) == GLOBAL_CAP


def test_retry_after_is_honored(env) -> None:
    class SlowDown(FakeLoginProvider):
        def login_start(self, user_id, email, password):
            e = RateLimited("429")
            e.retry_after = 5 * 3600  # type: ignore[attr-defined]
            raise e
    lm = env["make"](provider=SlowDown(env["tokens"], delay=0))
    lm.start("friend", "a@x.com", PASSWORD)
    assert lm.cooldown("friend")[0] - env["clock"].t == 5 * 3600


def test_cooldown_survives_restart(env) -> None:
    env["make"]().start("friend", "429@x.com", PASSWORD)
    fresh = env["make"]()
    with pytest.raises(Cooldown) as e:
        fresh.start("friend", "ok@x.com", PASSWORD)
    assert e.value.scope == "global"


def test_one_login_at_a_time_server_wide(env) -> None:
    run = Deferred()
    lm = env["make"](run=run)
    first = lm.start("friend", "ok@x.com", PASSWORD)
    assert first.state == "pending"
    with pytest.raises(Busy):
        lm.start("andrew", "ok@x.com", PASSWORD)
    run.drain()
    assert first.state == "ok"
    assert lm.start("andrew", "ok@x.com", PASSWORD).state == "pending"


def test_jobs_are_bound_to_their_user(env) -> None:
    lm = env["make"]()
    job = lm.start("friend", "mfa@x.com", PASSWORD)
    with pytest.raises(KeyError):
        lm.status("andrew", job.id)
    with pytest.raises(KeyError):
        lm.submit_mfa("andrew", job.id, "123456")
    assert lm.status("friend", job.id).state == "needs_mfa"


def test_unfinished_mfa_expires(env) -> None:
    lm, clock = env["make"](), env["clock"]
    job = lm.start("friend", "mfa@x.com", PASSWORD)
    clock.t += 601
    job = lm.status("friend", job.id)
    assert (job.state, job.error, job.handle) == ("error", "expired", None)


def test_password_is_dropped_and_never_logged(env, caplog) -> None:
    seen: list[str] = []

    class Spy(FakeLoginProvider):
        def login_start(self, user_id, email, password):
            seen.append(password)
            return super().login_start(user_id, "wrong@x.com", password)
    lm = env["make"](provider=Spy(env["tokens"], delay=0))
    with caplog.at_level(logging.DEBUG):
        job = lm.start("friend", "wrong@x.com", PASSWORD)
    assert seen == [PASSWORD]  # Garmin got it exactly once
    assert PASSWORD not in json.dumps(job.public()) and PASSWORD not in repr(job)
    assert PASSWORD not in caplog.text


def test_disconnect_deletes_tokens(env) -> None:
    lm = env["make"]()
    lm.start("friend", "ok@x.com", PASSWORD)
    lm.disconnect("friend", env["tokens"])
    assert env["tokens"].load("friend") is None
    assert env["changed"] == ["friend", "friend"]
