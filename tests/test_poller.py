from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from garmin_mcp.logs import JsonFormatter
from garmin_mcp.poller import (
    BACKOFF_BASE,
    Poller,
    PollerThread,
    RoutineClient,
    Schedule,
    StateStore,
)
from garmin_mcp.providers import ReauthRequired

TOKEN = "sk-ant-oat01-SECRETSECRET"


def act(i: int, **extra: Any) -> dict[str, Any]:
    return {"activityId": i, "activityType": {"typeKey": "running"},
            "startTimeLocal": f"2026-10-0{i % 9 + 1} 06:00:00", "duration": 3000.0,
            "distance": 10000.0, **extra}


class FakeGarmin:
    def __init__(self, acts: list[dict[str, Any]]) -> None:
        self.acts = acts  # newest first, like get_activities
        self.calls: list[tuple[int, int]] = []
        self.error: Exception | None = None

    def __call__(self, start: int, limit: int) -> list[dict[str, Any]]:
        self.calls.append((start, limit))
        if self.error:
            raise self.error
        return self.acts[start:start + limit]


class FakeRoutine:
    """Records fire requests; replies with queued responses (default 200)."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.replies: list[Any] = []
        self.client = RoutineClient("rt_1", TOKEN, http=httpx2.Client(
            transport=httpx2.MockTransport(self._handle)))

    def _handle(self, req: httpx2.Request) -> httpx2.Response:
        self.requests.append(req)
        reply = self.replies.pop(0) if self.replies else 200
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx2.Response):
            return reply
        return httpx2.Response(reply, json={
            "type": "routine_fire",
            "claude_code_session_url": "https://claude.ai/code/session_1"})

    def texts(self) -> list[str]:
        return [json.loads(r.content)["text"] for r in self.requests]


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path: Path):
    class Env:
        garmin = FakeGarmin([act(i) for i in (103, 102, 101)])
        routine = FakeRoutine()
        store = StateStore(tmp_path / "state.json")
        clock = Clock()
        fp = {"v": "a"}

        def poller(self, **kw: Any) -> Poller:
            return Poller(self.garmin, self.store, self.routine.client, clock=self.clock,
                          token_fingerprint=lambda: self.fp["v"], **kw)

        def seen(self) -> int | None:
            return self.store.load().last_seen_activity_id

    return Env()


def test_first_run_baselines_without_firing(env) -> None:
    out = env.poller().poll()
    assert out["baseline"] == 103 and out["triggered"] is False
    assert env.seen() == 103
    assert env.routine.requests == []


def test_no_new_activities(env) -> None:
    p = env.poller()
    p.poll()
    out = p.poll()
    assert out == {"checked": 3, "new": 0, "triggered": False}
    assert env.routine.requests == []


def test_two_new_fire_once_oldest_first(env) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(105), act(104), *env.garmin.acts]
    out = p.poll()
    assert out["new"] == 2 and out["triggered"] is True
    assert len(env.routine.requests) == 1
    req = env.routine.requests[0]
    assert str(req.url) == "https://api.anthropic.com/v1/claude_code/routines/rt_1/fire"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["anthropic-version"] == "2023-06-01"
    lines = env.routine.texts()[0].splitlines()
    assert [ln.split(" | ")[0] for ln in lines] == ["104", "105"]
    assert lines[0] == "104 | running | 2026-10-06 06:00:00 | 50:00 | 10.00 km"
    assert env.seen() == 105


def test_failed_fire_retries_next_poll(env) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    env.routine.replies = [500]
    out = p.poll()
    assert out["error"] == "routine_fire_failed" and out["status"] == 500
    assert env.seen() == 103  # cursor not advanced

    env.clock.t += BACKOFF_BASE
    out = p.poll()
    assert out["triggered"] is True
    assert len(env.routine.requests) == 2
    assert env.routine.texts()[0] == env.routine.texts()[1]
    assert env.seen() == 104


def test_429_honours_retry_after(env, caplog) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    env.routine.replies = [httpx2.Response(429, headers={"retry-after": "1200"})]
    with caplog.at_level(logging.INFO):
        p.poll()
    assert any(getattr(r, "fields", {}).get("retry_after_s") == 1200.0 for r in caplog.records)
    env.clock.t += 600
    assert p.poll()["skipped"] == "backoff"
    env.clock.t += 600
    assert p.poll()["triggered"] is True
    assert env.seen() == 104


def test_connect_error_retries_but_read_timeout_does_not(env) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    env.routine.replies = [httpx2.ConnectError("down")]
    p.poll()
    assert env.seen() == 103

    env.clock.t += BACKOFF_BASE
    env.routine.replies = [httpx2.ReadTimeout("slow")]
    out = p.poll()
    assert out["triggered"] == "unknown"
    assert env.seen() == 104  # may have started: don't risk a duplicate
    assert p.poll()["new"] == 0
    assert len(env.routine.requests) == 2


def test_crash_mid_fire_does_not_refire(env) -> None:
    env.poller().poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    state = env.store.load()
    state.pending_through = 104  # as if the process died after sending
    env.store.save(state)
    out = env.poller().poll()
    assert out["new"] == 0
    assert env.routine.requests == []
    assert env.seen() == 104


def test_multisport_children_skipped_parent_reported(env) -> None:
    p = env.poller()
    p.poll()
    parent = act(104, activityType={"typeKey": "multi_sport"}, isMultiSportParent=True,
                 childIds=[105, 106])
    env.garmin.acts = [act(106, parentId=104), act(105, parentId=104), parent,
                       *env.garmin.acts]
    p.poll()
    assert [ln.split(" | ")[0] for ln in env.routine.texts()[0].splitlines()] == ["104"]
    assert env.seen() == 106


def test_pages_past_five_new(env) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(i) for i in range(111, 100, -1)]  # 8 new above 103
    out = p.poll()
    assert env.garmin.calls[-2:] == [(0, 5), (5, 5)]
    assert out["new"] == 8
    assert env.seen() == 111


def test_dry_run_never_fires_or_writes(env) -> None:
    p = env.poller(dry_run=True)
    p.poll()
    assert env.seen() is None  # baseline not written in dry run
    env.poller().poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    out = p.poll()
    assert out["would_send"].startswith("104 | running")
    assert env.routine.requests == []
    assert env.seen() == 103


def test_daily_fire_cap(env) -> None:
    p = env.poller(max_fires_per_day=1)
    p.poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    p.poll()
    env.garmin.acts = [act(105), *env.garmin.acts]
    assert p.poll()["skipped"] == "daily_fire_cap"
    env.clock.t += 24 * 3600
    assert p.poll()["triggered"] is True


def test_reauth_backs_off_until_tokens_change(env) -> None:
    p = env.poller()
    p.poll()
    env.garmin.error = ReauthRequired("andrew")
    assert p.poll()["error"] == "reauth_required"
    env.garmin.error = None
    env.clock.t += 60
    assert p.poll()["skipped"] == "backoff"
    calls = len(env.garmin.calls)
    env.fp["v"] = "b"  # garmin-mcp-login wrote new tokens
    assert "skipped" not in p.poll()
    assert len(env.garmin.calls) == calls + 1


def test_corrupt_state_rebaselines(env) -> None:
    env.store.path.write_text("{not json")
    out = env.poller().poll()
    assert out["baseline"] == 103
    assert env.routine.requests == []


def test_token_never_logged(env, caplog) -> None:
    p = env.poller()
    p.poll()
    env.garmin.acts = [act(104), *env.garmin.acts]
    env.routine.replies = [httpx2.Response(401, json={"echo": TOKEN})]
    with caplog.at_level(logging.DEBUG):
        p.poll()
    fmt = JsonFormatter()
    assert all(TOKEN not in fmt.format(r) for r in caplog.records)


def test_schedule_fast_window_and_hourly() -> None:
    s = Schedule.parse("America/Denver", "5-9", 5)
    mdt = lambda h, m: datetime(2026, 10, 5, h, m, tzinfo=s.tz)  # noqa: E731
    assert s.next_after(mdt(5, 2)) == mdt(5, 5)
    assert s.next_after(mdt(9, 55)) == mdt(10, 0)
    assert s.next_after(mdt(10, 0)) == mdt(11, 0)
    assert s.next_after(mdt(4, 30)) == mdt(5, 0)
    assert s.next_after(mdt(23, 30)).astimezone(s.tz).hour == 0


def test_schedule_handles_dst_fall_back() -> None:
    s = Schedule.parse("America/Denver", "5-9", 5)
    # 2026-11-01 01:00 MDT -> 01:00 MST: the 01:00 hour happens twice.
    t = datetime(2026, 11, 1, 7, 30, tzinfo=UTC)  # 01:30 MDT
    nxt = s.next_after(t)
    assert nxt == datetime(2026, 11, 1, 8, 0, tzinfo=UTC)  # 01:00 MST, not skipped


def test_only_one_poller_thread_per_lock(tmp_path, env) -> None:
    s = Schedule.parse("America/Denver", "5-9", 5)
    a = PollerThread(env.poller(), s, tmp_path / "poller.lock")
    b = PollerThread(env.poller(), s, tmp_path / "poller.lock")
    try:
        assert a.start() is True
        assert b.start() is False
    finally:
        a.stop()
        b.stop()
