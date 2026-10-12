"""Rabbit's "Connect Garmin" flow: user-initiated, single-attempt Garmin logins.

Same rules as ``garmin-mcp-login`` (never automatic, never retried), enforced
in code because the trigger is a web form instead of an operator:

* One login in flight per server (Garmin rate-limits per IP and everyone shares it).
* Escalating per-user cooldown after rejected credentials: 1, 5, 15, 60 min;
  the ladder resets on success or after 24 h without failures.
* Global cooldown after a 429 or Cloudflare block: 60 min doubling to 24 h,
  never shorter than Garmin's Retry-After. Reports show 429s that look
  account-level and last 48 h+ after repeated attempts, so we back off hard.
* A wrong MFA code doesn't start a new login: up to 3 tries on the same handle.

The password is used for exactly one ``login_start`` call on a worker thread
and dropped; it is never stored, logged, or echoed. Jobs are bound to the
session's user_id, and only in memory (a restart abandons in-flight logins).

Cooldown state persists in the app store so a restart can't reset it.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .appstore import AppStore
from .logs import log_event
from .providers import CloudflareBlocked, GarminError, LoginHandle, LoginStatus, RateLimited
from .providers.base import GarminProvider

logger = logging.getLogger(__name__)

USER_LADDER = (60, 300, 900, 3600)
USER_LADDER_RESET = 24 * 3600
GLOBAL_BASE = 3600
GLOBAL_CAP = 24 * 3600
MFA_TRIES = 3
JOB_TTL = 600

Classifier = Callable[[BaseException], str]


@dataclass
class Job:
    id: str
    user_id: str
    state: str  # pending | needs_mfa | ok | error
    created: float
    error: str | None = None  # bad_credentials | bad_mfa | rate_limited | blocked | failed | expired
    mfa_tries_left: int = MFA_TRIES
    handle: LoginHandle | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {"job_id": self.id, "state": self.state, "error": self.error,
                "mfa_tries_left": self.mfa_tries_left if self.state == "needs_mfa" else None}


class Cooldown(Exception):
    def __init__(self, until: float, scope: str) -> None:
        super().__init__(scope)
        self.until = until
        self.scope = scope  # "user" | "global"


class Busy(Exception):
    pass


def classify_login_error(exc: BaseException) -> str:
    """Map a login exception to a coarse, user-safe category."""
    from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

    from .providers.unofficial import _probe, _status_of

    if isinstance(exc, CloudflareBlocked) or _probe.cloudflare:
        return "blocked"
    if isinstance(exc, RateLimited | GarminConnectTooManyRequestsError) or _status_of(exc) == 429:
        return "rate_limited"
    if isinstance(exc, GarminConnectAuthenticationError) or _status_of(exc) == 401:
        return "bad_credentials"
    return "failed"


class LoginManager:
    def __init__(self, provider: GarminProvider, store: AppStore, *,
                 classify: Classifier = classify_login_error,
                 on_tokens_changed: Callable[[str], None] = lambda user_id: None,
                 clock: Callable[[], float] = time.time,
                 run: Callable[[Callable[[], None]], None] | None = None) -> None:
        self._provider = provider
        self._classify = classify
        self._on_tokens_changed = on_tokens_changed
        self._clock = clock
        self._run = run or (lambda fn: threading.Thread(target=fn, daemon=True,
                                                        name="garmin-login").start())
        self._limits = store.global_doc("login_limits")
        self._lock = threading.Lock()
        self._inflight = threading.Lock()  # one Garmin login call at a time, server-wide
        self._jobs: dict[str, Job] = {}

    # ------------------------------------------------------------ cooldowns --
    def cooldown(self, user_id: str) -> tuple[float, str] | None:
        """(until, scope) if a login for this user is blocked right now."""
        data = self._limits.load()
        now = self._clock()
        g = data.get("global", {})
        if g.get("until", 0) > now:
            return float(g["until"]), "global"
        u = data.get("users", {}).get(user_id, {})
        if u.get("until", 0) > now:
            return float(u["until"]), "user"
        return None

    def _penalize_user(self, user_id: str) -> None:
        now = self._clock()

        def bump(data: dict[str, Any]) -> None:
            users = data.setdefault("users", {})
            u = users.get(user_id, {})
            fails = 0 if now - u.get("last_fail", 0) > USER_LADDER_RESET else u.get("fails", 0)
            delay = USER_LADDER[min(fails, len(USER_LADDER) - 1)]
            users[user_id] = {"fails": fails + 1, "last_fail": now, "until": now + delay}
        self._limits.update(bump)

    def _penalize_global(self, retry_after: float | None = None) -> None:
        now = self._clock()

        def bump(data: dict[str, Any]) -> None:
            g = data.get("global", {})
            recent = now - g.get("last", 0) < GLOBAL_CAP * 2
            delay = min(GLOBAL_CAP, g.get("delay", GLOBAL_BASE / 2) * 2) if recent else GLOBAL_BASE
            delay = max(delay, retry_after or 0)
            data["global"] = {"delay": delay, "last": now, "until": now + delay}
        self._limits.update(bump)

    def _clear_user(self, user_id: str) -> None:
        self._limits.update(lambda d: d.get("users", {}).pop(user_id, None))

    def disconnect(self, user_id: str, token_store: Any) -> None:
        token_store.delete(user_id)
        self._provider.invalidate(user_id)
        self._on_tokens_changed(user_id)
        log_event(logger, logging.INFO, "garmin_disconnected", user_id=user_id)

    # ----------------------------------------------------------------- jobs --
    def _prune(self) -> None:
        now = self._clock()
        for jid, job in list(self._jobs.items()):
            if now - job.created > JOB_TTL:
                if job.state in ("pending", "needs_mfa"):
                    job.state, job.error, job.handle = "error", "expired", None
                if now - job.created > 2 * JOB_TTL:
                    del self._jobs[jid]

    def start(self, user_id: str, email: str, password: str) -> Job:
        cd = self.cooldown(user_id)
        if cd:
            raise Cooldown(*cd)
        with self._lock:
            self._prune()
            if any(j.state in ("pending",) for j in self._jobs.values()) or self._inflight.locked():
                raise Busy()
            # A new login supersedes this user's unfinished MFA job.
            for j in self._jobs.values():
                if j.user_id == user_id and j.state == "needs_mfa":
                    j.state, j.error, j.handle = "error", "expired", None
            job = Job(secrets.token_urlsafe(16), user_id, "pending", self._clock())
            self._jobs[job.id] = job
        creds = [email, password]

        def work() -> None:
            with self._inflight:
                try:
                    result = self._provider.login_start(user_id, creds[0], creds[1])
                except Exception as e:
                    self._fail(job, e)
                    return
                finally:
                    creds.clear()  # drop the password as soon as Garmin has seen it
                if result.status is LoginStatus.NEEDS_MFA:
                    job.handle, job.state = result.handle, "needs_mfa"
                    log_event(logger, logging.INFO, "garmin_web_login_mfa", user_id=user_id)
                else:
                    self._succeed(job)
        self._run(work)
        return job

    def submit_mfa(self, user_id: str, job_id: str, code: str) -> Job:
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
            if job is None or job.user_id != user_id:
                raise KeyError(job_id)
            if job.state != "needs_mfa" or job.handle is None:
                return job
            if self._inflight.locked():
                raise Busy()
            job.state = "pending"
        handle = job.handle

        def work() -> None:
            with self._inflight:
                try:
                    self._provider.login_complete(handle, code)
                except Exception as e:
                    kind = self._classify(e)
                    if kind in ("bad_credentials", "failed") and job.mfa_tries_left > 1:
                        job.mfa_tries_left -= 1
                        job.state, job.error = "needs_mfa", "bad_mfa"
                        log_event(logger, logging.WARNING, "garmin_web_login_bad_mfa",
                                  user_id=user_id, tries_left=job.mfa_tries_left)
                        return
                    self._fail(job, e)
                    return
                self._succeed(job)
        self._run(work)
        return job

    def status(self, user_id: str, job_id: str) -> Job:
        with self._lock:
            self._prune()
            job = self._jobs.get(job_id)
        if job is None or job.user_id != user_id:
            raise KeyError(job_id)
        return job

    def _succeed(self, job: Job) -> None:
        job.state, job.error, job.handle = "ok", None, None
        self._clear_user(job.user_id)
        log_event(logger, logging.INFO, "garmin_web_login_ok", user_id=job.user_id)
        try:
            self._on_tokens_changed(job.user_id)
        except Exception as e:  # e.g. cache clear; never fail the login over it
            log_event(logger, logging.WARNING, "garmin_web_login_hook_failed",
                      error=type(e).__name__)

    def _fail(self, job: Job, exc: BaseException) -> None:
        kind = self._classify(exc)
        job.state, job.error, job.handle = "error", kind, None
        if kind in ("rate_limited", "blocked"):
            self._penalize_global(getattr(exc, "retry_after", None))
        else:
            self._penalize_user(job.user_id)
        # Type and category only: exception text can carry request details.
        log_event(logger, logging.WARNING, "garmin_web_login_failed", user_id=job.user_id,
                  kind=kind, error=type(exc).__name__)


# Dev-only scripted provider (logins + data) lives with the other providers.
from .providers.fake import FakeGarminProvider as FakeLoginProvider  # noqa: E402,F401
