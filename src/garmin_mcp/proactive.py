"""Rabbit's proactive messages: workout feedback, morning brief, reconnect nudge.

Runs in the Rabbit process only (never the MCP server). A daemon thread ticks
once a minute and, for each allowlisted user with Garmin tokens:

* **Workout feedback** reuses ``poller.Poller`` unchanged (cursor, at-most-once
  delivery, backoff, daily fire cap), one instance and state file per user.
  Its "routine" is an adapter that runs a proactive agent turn and pushes it.
* **Morning brief**: once per local date at the user's ``brief_time``. The date
  is recorded *before* sending (at-most-once); a definite failure (model
  unreachable, a chat turn in progress) clears it for a retry, up to 3 tries.
  Briefs more than 3 hours late are skipped rather than sent stale.
* **Reconnect nudge**: one push when Garmin rejects the user's tokens; reset
  when they connect again.

Delivery: the agent turn is stored in the user's chat history (``kind:
"proactive"``), then a push with the first lines of the message goes out.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .appstore import AppStore
from .logs import log_event
from .poller import FireOutcome, FireResult, Poller, Schedule, StateStore, token_fingerprint
from .tokens import TokenStore
from .tools import GarminService
from .webchat import ChatRunner, TurnBusy

logger = logging.getLogger(__name__)

DEFAULT_BRIEF_TIME = "06:30"
BRIEF_MAX_LATE = timedelta(hours=3)
BRIEF_TRIES = 3
TURN_TIMEOUT = 180.0

WORKOUT_PROMPT = (
    "A new activity just synced from Garmin (id | sport | start | duration | distance):\n"
    "{lines}\n\n"
    "Write a short note to the athlete about it: 2-3 short sentences, under 60 words. Say what "
    "stood out (pace, power, HR, effort vs. what today's planned session was for), and give one "
    "practical note about the next day or two of their plan. Look up details or recent load/HRV "
    "with tools if that helps. Don't open with a greeting. This will be sent as a phone notification."
)
BRIEF_PROMPT = (
    "Write this morning's brief for the athlete. Check last night's sleep, HRV and training "
    "readiness, and yesterday's training if any, then decide whether today's planned session "
    "(from the context) should go as planned or be eased.\n"
    "Format, under 50 words in total:\n"
    "- First line: today's session in a few words, in bold, then the call. For example: "
    "**Bike 40' easy + 25' strength.** Go as planned. On a planned rest day, say it's a rest day "
    "and give no workout.\n"
    "- Then 1-2 short bullets with the reason, with numbers where you have them.\n"
    "Sleep, HRV and readiness often haven't synced from the watch this early. If they're "
    "missing, make one bullet \"Sleep hasn't synced yet\" and base the call on the plan and "
    "recent training; don't explain further. Don't restate the plan or open with a greeting. "
    "This will be sent as a phone notification."
)

Deliver = Callable[[str, str, str], FireResult]  # (user_id, kind, prompt) -> result
Notify = Callable[[str, str, str], None]  # (user_id, title, body)


def push_preview(text: str, limit: int = 180) -> str:
    """Plain-text notification body from the agent's markdown."""
    t = re.sub(r"[*_`#>]+", "", text)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


class _Routine:
    """Adapts Poller's routine interface to an agent turn."""

    def __init__(self, user_id: str, deliver: Deliver) -> None:
        self._user = user_id
        self._deliver = deliver

    def fire(self, text: str) -> FireResult:
        return self._deliver(self._user, "workout", WORKOUT_PROMPT.format(lines=text))


class Proactive:
    def __init__(self, *, service: GarminService, store: AppStore, token_store: TokenStore,
                 users: Callable[[], list[str]], deliver: Deliver, notify: Notify,
                 schedule: Schedule, state_dir: Path, default_tz: str,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._service = service
        self._store = store
        self._tokens = token_store
        self._users = users
        self._deliver = deliver
        self._notify = notify
        self._schedule = schedule
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._tz = default_tz
        self._clock = clock
        self._pollers: dict[str, Poller] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- helpers --
    def _profile(self, user_id: str) -> dict[str, Any]:
        return self._store.user_doc(user_id, "profile").load()

    def _zone(self, profile: dict[str, Any]) -> ZoneInfo:
        try:
            return ZoneInfo(profile.get("timezone") or self._tz)
        except Exception:
            return ZoneInfo(self._tz)

    def poller(self, user_id: str) -> Poller:
        with self._lock:
            p = self._pollers.get(user_id)
            if p is None:
                def fetch(start: int, limit: int, _u: str = user_id) -> list[dict[str, Any]]:
                    return self._service.read_uncached(_u, "get_activities",
                                                       {"start": start, "limit": limit})
                p = self._pollers[user_id] = Poller(
                    fetch, StateStore(self._state_dir / f"{user_id}.json"),
                    _Routine(user_id, self._deliver),  # type: ignore[arg-type]
                    token_fingerprint=token_fingerprint(lambda _u=user_id: self._tokens.load(_u)),
                )
            return p

    def _connected(self, user_id: str) -> bool:
        try:
            return bool(self._tokens.load(user_id))
        except Exception:
            return False

    # ---------------------------------------------------------------- tick --
    def tick(self) -> None:
        now = self._clock()
        for user_id in self._users():
            if not self._connected(user_id):
                continue
            try:
                profile = self._profile(user_id)
                # The fast-poll window (e.g. 5-9am) runs in each person's own time zone.
                schedule = dataclasses.replace(self._schedule, tz=self._zone(profile))
                if profile.get("workout_feedback", True) and schedule.matches(now):
                    self.poll(user_id)
                if profile.get("brief_enabled", True):
                    self._maybe_brief(user_id, profile, now)
            except Exception as e:  # one user's problem never stops the others
                log_event(logger, logging.ERROR, "proactive_error", user_id=user_id,
                          error=type(e).__name__)

    def poll(self, user_id: str) -> dict[str, Any]:
        summary = self.poller(user_id).poll()
        if summary.get("error") == "reauth_required":
            self._nudge_reconnect(user_id)
        return summary

    # -------------------------------------------------------------- brief --
    def _maybe_brief(self, user_id: str, profile: dict[str, Any], now: datetime) -> None:
        local = now.astimezone(self._zone(profile))
        try:
            hh, mm = (int(x) for x in str(profile.get("brief_time") or DEFAULT_BRIEF_TIME).split(":"))
            due = datetime.combine(local.date(), dtime(hh, mm), local.tzinfo)
        except (ValueError, TypeError):
            return
        if local < due or local - due > BRIEF_MAX_LATE:
            return
        today = local.date().isoformat()
        doc = self._store.user_doc(user_id, "proactive")

        def claim(d: dict[str, Any]) -> bool:
            if d.get("brief_date") == today:
                return False
            if d.get("brief_try_date") != today:
                d["brief_try_date"], d["brief_tries"] = today, 0
            if d["brief_tries"] >= BRIEF_TRIES:
                return False
            d["brief_tries"] += 1
            d["brief_date"] = today  # claimed before sending: at most once
            return True
        if not doc.update(claim):
            return
        self.send_brief(user_id, today)

    def send_brief(self, user_id: str, day: str | None = None) -> FireResult:
        result = self._deliver(user_id, "brief", BRIEF_PROMPT)
        if result.outcome is FireOutcome.FAILED and day is not None:
            self._store.user_doc(user_id, "proactive").update(
                lambda d: d.pop("brief_date", None) if d.get("brief_date") == day else None)
        log_event(logger, logging.INFO, "brief", user_id=user_id, outcome=result.outcome.value)
        return result

    # ----------------------------------------------------------- reconnect --
    def _nudge_reconnect(self, user_id: str) -> None:
        def mark(d: dict[str, Any]) -> bool:
            if d.get("reauth_notified"):
                return False
            d["reauth_notified"] = True
            return True
        if self._store.user_doc(user_id, "proactive").update(mark):
            self._notify(user_id, "Garmin disconnected",
                         "Rabbit can't see your training right now. Tap to reconnect Garmin.")
            log_event(logger, logging.INFO, "reauth_nudge", user_id=user_id)

    def on_connected(self, user_id: str) -> None:
        self._store.user_doc(user_id, "proactive").update(lambda d: d.pop("reauth_notified", None))


# ------------------------------------------------------------ delivery ------
class AgentDelivery:
    """Runs a proactive agent turn on the server's event loop (from the tick
    thread), then pushes a preview. Maps outcomes onto the poller's semantics."""

    def __init__(self, runner: ChatRunner, push: Any, loop_getter: Callable[[], Any]) -> None:
        self._runner = runner
        self._push = push
        self._loop = loop_getter

    def __call__(self, user_id: str, kind: str, prompt: str) -> FireResult:
        loop = self._loop()
        if loop is None:
            return FireResult(FireOutcome.FAILED)

        async def go() -> dict[str, Any] | None:
            # Proactive messages land in the active goal's pinned coach thread.
            agent = self._runner.agent
            try:  # know today's planned session before writing (cached; about once a week)
                await asyncio.wait_for(agent.planner.ensure(user_id), timeout=60)
            except Exception as e:
                log_event(logger, logging.WARNING, "proactive_plan_unavailable", user_id=user_id,
                          error=type(e).__name__)
            thread = agent.coach.coach_thread(user_id)
            turn = self._runner.start(user_id, prompt, thread_id=thread["id"],
                                      kind="proactive", label=kind)
            return await turn.wait()
        fut = asyncio.run_coroutine_threadsafe(go(), loop)
        try:
            record = fut.result(timeout=TURN_TIMEOUT)
        except TurnBusy:
            return FireResult(FireOutcome.FAILED)  # the user is mid-chat: try next tick
        except Exception as e:
            log_event(logger, logging.WARNING, "proactive_delivery_unknown", user_id=user_id,
                      kind=kind, error=type(e).__name__)
            return FireResult(FireOutcome.UNKNOWN)
        text = (record or {}).get("assistant") or ""
        if not text:
            return FireResult(FireOutcome.FAILED)  # model unreachable: nothing was delivered
        if self._push is not None:
            title = "Morning brief" if kind == "brief" else "Rabbit"
            try:
                self._push.send(user_id, title, push_preview(text), url="/app/?open=coach")
            except Exception as e:  # the message is already in chat history
                log_event(logger, logging.WARNING, "proactive_push_failed", user_id=user_id,
                          error=type(e).__name__)
        return FireResult(FireOutcome.OK, 200)


class ProactiveThread:
    """Ticks once a minute. Stops cleanly with the app."""

    def __init__(self, proactive: Proactive) -> None:
        self._p = proactive
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rabbit-proactive", daemon=True)
        self._thread.start()
        log_event(logger, logging.INFO, "proactive_started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(UTC)
            nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
            if self._stop.wait((nxt - now).total_seconds()):
                return
            try:
                self._p.tick()
            except Exception as e:
                log_event(logger, logging.ERROR, "proactive_tick_error", error=type(e).__name__)
