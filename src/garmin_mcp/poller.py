"""Workout-sync poller: detect new Garmin activities, fire a Claude Code routine.

Runs as a background thread inside the server process, so it shares the
provider's per-user session and lock with the MCP tools (Garmin rotates the
refresh token on every refresh; a second process could race it).

Delivery is at-most-once per activity:
  * The cursor (``last_seen_activity_id``) advances only after the routine
    answers 200, so definite failures (any HTTP error response, connect
    errors) are retried on the next poll.
  * ``/fire`` has no idempotency key. When the outcome is unknown (read
    timeout, connection dropped after sending, or a crash between sending and
    saving), the cursor advances anyway and a warning is logged, rather than
    risk a duplicate review.
  * A file lock allows one poller per data dir, and a daily fire cap is a
    circuit breaker against a bug that refires every poll.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2

from .logs import log_event
from .providers import GarminError, RateLimited, ReauthRequired
from .tools import _fmt_duration

logger = logging.getLogger(__name__)

ROUTINE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"
PAGE_SIZE = 5
MAX_PAGES = 3
MAX_TEXT = 65_536  # documented limit for the fire endpoint's `text`
BACKOFF_BASE = 300.0
BACKOFF_CAP = 3600.0
REAUTH_BACKOFF_CAP = 6 * 3600.0
DAY = 24 * 3600.0


# ------------------------------------------------------------------ state ---
@dataclass
class PollState:
    last_seen_activity_id: int | None = None
    updated_at: str = ""
    # Set just before a fire and cleared after it; still set on load means we
    # crashed mid-fire, so the outcome is unknown.
    pending_through: int | None = None
    fires: list[float] = field(default_factory=list)  # epoch seconds, last 24h


class StateStore:
    """One small JSON file, written atomically (temp file + fsync + rename)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> PollState:
        try:
            raw = json.loads(self.path.read_text())
            return PollState(
                last_seen_activity_id=_int_or_none(raw.get("last_seen_activity_id")),
                updated_at=str(raw.get("updated_at", "")),
                pending_through=_int_or_none(raw.get("pending_through")),
                fires=[float(t) for t in raw.get("fires", [])],
            )
        except FileNotFoundError:
            return PollState()
        except (ValueError, TypeError, AttributeError, OSError):
            # Treated as a first run: re-baselines without firing. Worst case is
            # a missed review, never a duplicate.
            log_event(logger, logging.WARNING, "poll_state_unreadable", path=str(self.path))
            return PollState()

    def save(self, state: PollState) -> None:
        state.updated_at = datetime.now(UTC).isoformat()
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(state), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def _int_or_none(v: Any) -> int | None:
    return None if v is None else int(v)


# ---------------------------------------------------------------- routine ---
class FireOutcome(Enum):
    OK = "ok"            # 200: routine started
    FAILED = "failed"    # definitely not started; retry next poll
    UNKNOWN = "unknown"  # may have started; do not retry


@dataclass
class FireResult:
    outcome: FireOutcome
    status: int | None = None
    retry_after: float | None = None
    session_url: str | None = None


class RoutineClient:
    def __init__(self, routine_id: str, token: str, *, http: httpx2.Client | None = None,
                 timeout: float = 30.0) -> None:
        self._url = ROUTINE_URL.format(routine_id=routine_id)
        self._token = token
        self._http = http or httpx2.Client(timeout=timeout)

    def fire(self, text: str) -> FireResult:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        try:
            resp = self._http.post(self._url, headers=headers, json={"text": text[:MAX_TEXT]})
        except (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout) as e:
            # Never reached the server.
            log_event(logger, logging.WARNING, "routine_fire_unreachable", error=type(e).__name__)
            return FireResult(FireOutcome.FAILED)
        except httpx2.HTTPError as e:
            # Sent (or partly sent) but no usable response: may have started.
            log_event(logger, logging.WARNING, "routine_fire_no_response", error=type(e).__name__)
            return FireResult(FireOutcome.UNKNOWN)
        if resp.status_code == 200:
            url = None
            with contextlib.suppress(ValueError, AttributeError):
                url = resp.json().get("claude_code_session_url")
            return FireResult(FireOutcome.OK, 200, session_url=url)
        retry_after = None
        if resp.status_code == 429:
            ra = resp.headers.get("retry-after", "")
            retry_after = float(ra) if ra.isdigit() else None
        # Status only: error bodies are not logged.
        return FireResult(FireOutcome.FAILED, resp.status_code, retry_after=retry_after)


# ----------------------------------------------------------------- poller ---
FetchPage = Callable[[int, int], list[dict[str, Any]]]


def activity_line(a: dict[str, Any]) -> str:
    atype = a.get("activityType")
    sport = atype.get("typeKey") if isinstance(atype, dict) else None
    dist = a.get("distance")
    km = f"{dist / 1000:.2f} km" if isinstance(dist, int | float) and dist else "-"
    return " | ".join(str(x) for x in (
        a.get("activityId"), sport or "-", a.get("startTimeLocal") or "-",
        _fmt_duration(a.get("duration")) or "-", km,
    ))


class Poller:
    def __init__(
        self,
        fetch_page: FetchPage,
        store: StateStore,
        routine: RoutineClient | None,
        *,
        dry_run: bool = False,
        max_fires_per_day: int = 10,
        token_fingerprint: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch = fetch_page
        self._store = store
        self._routine = routine
        self._dry_run = dry_run
        self._max_fires = max_fires_per_day
        self._token_fp = token_fingerprint
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._not_before = 0.0
        self._reauth_fp: str | None = None

    # -- backoff -------------------------------------------------------------
    def _fail(self, *, retry_after: float | None = None, reauth: bool = False) -> None:
        self._failures += 1
        cap = REAUTH_BACKOFF_CAP if reauth else BACKOFF_CAP
        delay = min(cap, BACKOFF_BASE * 2 ** (self._failures - 1))
        if retry_after:
            delay = max(delay, retry_after)
        self._not_before = self._clock() + delay
        self._reauth_fp = self._token_fp() if reauth else None

    def _ok(self) -> None:
        self._failures, self._not_before, self._reauth_fp = 0, 0.0, None

    def _backing_off(self) -> bool:
        if self._reauth_fp is not None and self._token_fp() != self._reauth_fp:
            self._ok()  # garmin-mcp-login wrote new tokens: resume right away
        return self._clock() < self._not_before

    # -- one poll ------------------------------------------------------------
    def poll(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return self._log({"skipped": "poll_in_progress"})
        try:
            if self._backing_off():
                return self._log({"skipped": "backoff",
                                  "retry_in_s": round(self._not_before - self._clock())})
            return self._log(self._poll())
        finally:
            self._lock.release()

    def _poll(self) -> dict[str, Any]:
        state = self._store.load()
        if state.pending_through is not None and not self._dry_run:
            # Crashed between sending a fire and recording its result.
            log_event(logger, logging.WARNING, "routine_fire_outcome_unknown",
                      through_id=state.pending_through, reason="interrupted")
            state.last_seen_activity_id = max(state.last_seen_activity_id or 0,
                                              state.pending_through)
            state.pending_through = None
            self._store.save(state)

        try:
            acts = self._fetch_new(state.last_seen_activity_id)
        except ReauthRequired:
            self._fail(reauth=True)
            return {"error": "reauth_required"}
        except RateLimited:
            self._fail()
            return {"error": "garmin_rate_limited"}
        except GarminError as e:
            self._fail()
            return {"error": type(e).__name__}

        checked = len(acts)
        ids = [i for i in (_activity_id(a) for a in acts) if i is not None]
        if not ids:
            self._ok()
            return {"checked": 0, "new": 0, "triggered": False}
        newest = max(ids)

        if state.last_seen_activity_id is None:
            if not self._dry_run:
                state.last_seen_activity_id = newest
                self._store.save(state)
            self._ok()
            return {"checked": checked, "new": 0, "triggered": False, "baseline": newest}

        last = state.last_seen_activity_id
        new = sorted(
            (a for a in acts
             if (_activity_id(a) or 0) > last and not a.get("parentId")),  # multisport children
            key=lambda a: _activity_id(a) or 0,
        )
        if not new:
            if newest > last and not self._dry_run:
                state.last_seen_activity_id = newest  # only children were new
                self._store.save(state)
            self._ok()
            return {"checked": checked, "new": 0, "triggered": False}

        text = "\n".join(activity_line(a) for a in new)
        summary: dict[str, Any] = {"checked": checked, "new": len(new), "triggered": False}
        if self._dry_run:
            self._ok()
            return {**summary, "would_send": text}

        now = self._clock()
        state.fires = [t for t in state.fires if now - t < DAY]
        if len(state.fires) >= self._max_fires:
            log_event(logger, logging.ERROR, "routine_fire_cap_reached", cap=self._max_fires)
            return {**summary, "skipped": "daily_fire_cap"}
        if self._routine is None:
            return {**summary, "error": "routine_not_configured"}

        state.pending_through = newest
        self._store.save(state)
        result = self._routine.fire(text)
        state.pending_through = None
        summary["status"] = result.status

        if result.outcome is FireOutcome.FAILED:
            self._store.save(state)
            if result.retry_after is not None:
                log_event(logger, logging.WARNING, "routine_rate_limited",
                          retry_after_s=result.retry_after)
            self._fail(retry_after=result.retry_after)
            return {**summary, "error": "routine_fire_failed"}

        state.last_seen_activity_id = newest
        state.fires.append(now)
        self._store.save(state)
        self._ok()
        if result.outcome is FireOutcome.UNKNOWN:
            log_event(logger, logging.WARNING, "routine_fire_outcome_unknown",
                      through_id=newest, reason="no_response")
            return {**summary, "triggered": "unknown"}
        return {**summary, "triggered": True, "session_url": result.session_url}

    def _fetch_new(self, last: int | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(MAX_PAGES if last is not None else 1):
            batch = [a for a in (self._fetch(page * PAGE_SIZE, PAGE_SIZE) or [])
                     if isinstance(a, dict)]
            out.extend(batch)
            ids = [i for i in (_activity_id(a) for a in batch) if i is not None]
            if len(batch) < PAGE_SIZE or not ids or min(ids) <= (last or 0):
                break
        return out

    def _log(self, summary: dict[str, Any]) -> dict[str, Any]:
        fields = {k: v for k, v in summary.items() if k != "would_send"}
        log_event(logger, logging.INFO, "poll", dry_run=self._dry_run, **fields)
        if "would_send" in summary:
            log_event(logger, logging.INFO, "poll_dry_run_text", text=summary["would_send"])
        return summary


def _activity_id(a: dict[str, Any]) -> int | None:
    v = a.get("activityId")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def token_fingerprint(load: Callable[[], str | None]) -> Callable[[], str | None]:
    def fp() -> str | None:
        try:
            blob = load()
        except Exception:
            return None
        return hashlib.sha256(blob.encode()).hexdigest() if blob else None
    return fp


# --------------------------------------------------------------- schedule ---
@dataclass(frozen=True)
class Schedule:
    """Every ``fast_minutes`` during ``fast_hours`` (local), on the hour otherwise."""

    tz: ZoneInfo
    fast_hours: frozenset[int]
    fast_minutes: int = 5

    @classmethod
    def parse(cls, tz: str, fast_hours: str, fast_minutes: int) -> Schedule:
        hours: set[int] = set()
        for part in fast_hours.split(","):
            part = part.strip()
            if not part:
                continue
            lo, _, hi = part.partition("-")
            hours.update(range(int(lo), int(hi or lo) + 1))
        if not 1 <= fast_minutes <= 60 or not hours <= set(range(24)):
            raise RuntimeError("invalid POLL_FAST_HOURS / POLL_FAST_MINUTES")
        return cls(ZoneInfo(tz), frozenset(hours), fast_minutes)

    def matches(self, t: datetime) -> bool:
        local = t.astimezone(self.tz)
        if local.hour in self.fast_hours:
            return local.minute % self.fast_minutes == 0
        return local.minute == 0

    def next_after(self, now: datetime) -> datetime:
        # Walk whole UTC minutes so DST transitions can't skip or repeat a slot.
        t = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(2 * 24 * 60):
            if self.matches(t):
                return t
            t += timedelta(minutes=1)
        raise AssertionError("unreachable")  # pragma: no cover


class PollerThread:
    """Daemon thread: sleep until the next slot, poll, repeat. Never dies on errors."""

    def __init__(self, poller: Poller, schedule: Schedule, lock_path: Path) -> None:
        self._poller = poller
        self._schedule = schedule
        self._lock_path = Path(lock_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_fd: int | None = None

    def start(self) -> bool:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            log_event(logger, logging.ERROR, "poller_already_running", lock=str(self._lock_path))
            return False
        self._lock_fd = fd
        self._thread = threading.Thread(target=self._run, name="garmin-poller", daemon=True)
        self._thread.start()
        log_event(logger, logging.INFO, "poller_started",
                  next_run=self._schedule.next_after(datetime.now(UTC)).isoformat())
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # releases the flock
            self._lock_fd = None

    def _run(self) -> None:
        while not self._stop.is_set():
            target = self._schedule.next_after(datetime.now(UTC))
            # Re-check periodically so a wall-clock jump can't oversleep a slot.
            while not self._stop.is_set():
                remaining = (target - datetime.now(UTC)).total_seconds()
                if remaining <= 0:
                    break
                self._stop.wait(min(remaining, 60))
            if self._stop.is_set():
                return
            try:
                self._poller.poll()
            except Exception as e:  # keep the loop alive no matter what
                log_event(logger, logging.ERROR, "poll_unexpected_error", error=type(e).__name__)
