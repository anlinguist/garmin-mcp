"""Rabbit proactive messages: workout feedback, morning brief, reconnect nudge."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from garmin_mcp.agent import Agent
from garmin_mcp.appstore import AppStore
from garmin_mcp.poller import FireOutcome, FireResult, Schedule
from garmin_mcp.proactive import AgentDelivery, Proactive, push_preview
from garmin_mcp.providers import ReauthRequired
from garmin_mcp.providers import fake as fake_mod
from garmin_mcp.providers.fake import FakeGarminProvider, simulate_activity
from garmin_mcp.registry import Registry
from garmin_mcp.resilience import RateLimiter, TTLCache
from garmin_mcp.tokens import EncryptedFileTokenStore
from garmin_mcp.tools import GarminService
from garmin_mcp.webchat import ChatRunner
from tests.test_agent import Scripted


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry()


@pytest.fixture(autouse=True)
def _reset_simulated():
    fake_mod._SIMULATED.clear()
    yield
    fake_mod._SIMULATED.clear()


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@pytest.fixture
def env(tmp_path, fernet_key, registry):
    tokens = EncryptedFileTokenStore(tmp_path / "tokens", fernet_key)
    tokens.save("friend", '{"fake": true}')
    store = AppStore(tmp_path / "app", fernet_key)
    provider = FakeGarminProvider(tokens, delay=0)
    service = GarminService(provider, registry, allow_writes=False, cache=TTLCache(300),
                            limiter=RateLimiter(1000), max_response_bytes=60_000)
    delivered: list[tuple[str, str, str]] = []
    notified: list[tuple[str, str, str]] = []
    outcome = {"next": FireResult(FireOutcome.OK, 200)}
    clock = Clock(datetime(2026, 10, 12, 4, 0, tzinfo=UTC))

    def deliver(user_id: str, kind: str, prompt: str) -> FireResult:
        delivered.append((user_id, kind, prompt))
        return outcome["next"]

    def make(users=("friend",), svc=None) -> Proactive:
        return Proactive(service=svc or service, store=store, token_store=tokens,
                         users=lambda: list(users), deliver=deliver,
                         notify=lambda u, t, b: notified.append((u, t, b)),
                         schedule=Schedule.parse("UTC", "0-23", 1),
                         state_dir=tmp_path / "poller", default_tz="UTC", clock=clock)
    return SimpleNamespace(tokens=tokens, store=store, service=service, provider=provider,
                           delivered=delivered, notified=notified, outcome=outcome,
                           clock=clock, make=make, registry=registry)


def _workouts(env) -> list[tuple[str, str, str]]:
    return [d for d in env.delivered if d[1] == "workout"]


def _profile(env, user: str = "friend", **kw: Any) -> None:
    env.store.user_doc(user, "profile").update(lambda p: p.update(kw))


# ---------------------------------------------------------------- workouts --
def test_new_workout_delivered_once(env) -> None:
    _profile(env, brief_enabled=False)
    p = env.make()
    p.tick()  # first poll only records a baseline
    assert _workouts(env) == []
    act = simulate_activity()
    p.tick()
    (user, kind, prompt), = _workouts(env)
    assert user == "friend" and str(act["activityId"]) in prompt and "running" in prompt
    p.tick()
    assert len(_workouts(env)) == 1  # at most once


def test_failed_delivery_retries_next_poll(env) -> None:
    _profile(env, brief_enabled=False)
    p = env.make()
    p.tick()
    simulate_activity()
    env.outcome["next"] = FireResult(FireOutcome.FAILED)
    p.poll("friend")
    env.outcome["next"] = FireResult(FireOutcome.OK, 200)
    p.poller("friend")._ok()  # skip the 5-minute backoff for the test
    p.poll("friend")
    p.poll("friend")
    assert len(_workouts(env)) == 2  # one failed attempt, one success, then nothing


def test_workout_feedback_can_be_turned_off(env) -> None:
    _profile(env, brief_enabled=False, workout_feedback=False)
    p = env.make()
    p.tick()
    simulate_activity()
    p.tick()
    assert _workouts(env) == []


def test_unconnected_users_are_skipped(env) -> None:
    p = env.make(users=("friend", "andrew"))  # andrew has no Garmin tokens
    p.tick()
    simulate_activity()
    p.tick()
    assert {d[0] for d in env.delivered} == {"friend"}


# ------------------------------------------------------------------ brief ---
def test_brief_once_per_day_at_local_time(env) -> None:
    _profile(env, workout_feedback=False, timezone="America/Los_Angeles", brief_time="06:30")
    p = env.make()
    env.clock.t = datetime(2026, 10, 12, 13, 29, tzinfo=UTC)  # 06:29 PT
    p.tick()
    assert env.delivered == []
    env.clock.t += timedelta(minutes=1)  # 06:30 PT
    p.tick()
    p.tick()
    assert [d[1] for d in env.delivered] == ["brief"]
    env.clock.t += timedelta(days=1)
    p.tick()
    assert [d[1] for d in env.delivered] == ["brief", "brief"]


def test_stale_brief_is_skipped(env) -> None:
    _profile(env, workout_feedback=False, brief_time="06:30")
    p = env.make()
    env.clock.t = datetime(2026, 10, 12, 9, 31, tzinfo=UTC)  # 3h01m late (UTC user)
    p.tick()
    assert env.delivered == []


def test_brief_retries_failures_then_gives_up(env) -> None:
    _profile(env, workout_feedback=False, brief_time="06:30")
    p = env.make()
    env.outcome["next"] = FireResult(FireOutcome.FAILED)
    env.clock.t = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)
    for _ in range(5):
        p.tick()
        env.clock.t += timedelta(minutes=1)
    assert len(env.delivered) == 3


def test_unknown_brief_outcome_is_not_resent(env) -> None:
    _profile(env, workout_feedback=False, brief_time="06:30")
    p = env.make()
    env.outcome["next"] = FireResult(FireOutcome.UNKNOWN)
    env.clock.t = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)
    p.tick()
    env.clock.t += timedelta(minutes=1)
    p.tick()
    assert len(env.delivered) == 1


def test_brief_can_be_turned_off(env) -> None:
    _profile(env, workout_feedback=False, brief_enabled=False)
    p = env.make()
    env.clock.t = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)
    p.tick()
    assert env.delivered == []


# --------------------------------------------------------------- reconnect --
def test_reconnect_nudge_once_until_reconnected(env) -> None:
    class Rejecting(FakeGarminProvider):
        def get_client(self, user_id):
            raise ReauthRequired(user_id, "Garmin rejected the token")
    svc = GarminService(Rejecting(env.tokens), env.registry, allow_writes=False,
                        cache=TTLCache(1), limiter=RateLimiter(100), max_response_bytes=1000)
    _profile(env, brief_enabled=False)
    p = env.make(svc=svc)
    p.poll("friend")
    p.poller("friend")._ok()
    p.poll("friend")
    assert [n[1] for n in env.notified] == ["Garmin disconnected"]
    p.on_connected("friend")
    p.poller("friend")._ok()
    p.poll("friend")
    assert len(env.notified) == 2


# ---------------------------------------------------------------- delivery --
def test_push_preview_is_plain_text() -> None:
    md = "**Nice tempo run!** You held [4:45/km](x) for `3x10`.\n\n- Easy tomorrow"
    assert push_preview(md) == "Nice tempo run! You held 4:45/km for 3x10. - Easy tomorrow"
    assert push_preview("x" * 300).endswith("…") and len(push_preview("x" * 300)) == 180


def test_agent_delivery_runs_turn_and_pushes(env) -> None:
    pushes: list[tuple[str, str, str]] = []
    push = SimpleNamespace(send=lambda u, t, b: pushes.append((u, t, b)))
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        backend = Scripted([(["**Strong** tempo. Easy spin tomorrow."], [], "end_turn"),
                            ([], [], "end_turn")])
        agent = Agent(env.service, env.store, backend, daily_token_budget=100_000)
        runner = ChatRunner(agent)
        deliver = AgentDelivery(runner, push, lambda: loop)
        res = deliver("friend", "workout", "New activity ...")
        assert res.outcome is FireOutcome.OK
        assert pushes == [("friend", "Rabbit", "Strong tempo. Easy spin tomorrow.")]
        coach = agent.coach.coach_thread("friend")
        turn = agent.history("friend", coach["id"])[-1]
        assert turn["kind"] == "proactive" and turn["user"] is None
        assert turn["label"] == "workout"  # lands in the pinned coach log

        # no text produced (e.g. model unreachable): nothing delivered, retry later
        assert deliver("friend", "brief", "brief").outcome is FireOutcome.FAILED
        assert len(pushes) == 1
        assert AgentDelivery(runner, push, lambda: None)("friend", "brief", "x").outcome \
            is FireOutcome.FAILED
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=5)
