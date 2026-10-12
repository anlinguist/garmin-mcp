"""Fake Garmin provider for Rabbit dev (RABBIT_FAKE_GARMIN=1). No network, ever.

Logins are scripted by email (see ``login_start``). Once "connected", reads go
through the real registry and ``GarminService`` (validation, caching, shaping),
and only the final call is answered here, keyed by the real method name, from
~4 weeks of deterministic 70.3 base-phase training plus daily health metrics.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from .base import (
    CloudflareBlocked,
    GarminError,
    GarminProvider,
    GarminSession,
    LoginHandle,
    LoginResult,
    LoginStatus,
    RateLimited,
    ReauthRequired,
)

DAYS = 28
# weekday -> (sport, name, minutes, intensity 0..1); None = rest day
WEEK = {
    0: None,
    1: ("running", "Track intervals 6x800", 55, 0.8),
    2: ("indoor_cycling", "Trainer sweet spot 3x12", 65, 0.7),
    3: ("lap_swimming", "Pool swim 2500", 50, 0.6),
    4: ("running", "Easy run", 40, 0.3),
    5: ("cycling", "Long ride", 150, 0.5),
    6: ("running", "Long run", 75, 0.45),
}


def _rng(*parts: Any) -> random.Random:
    return random.Random("|".join(str(p) for p in parts))


def _activity(day: date, idx: int) -> dict[str, Any] | None:
    plan = WEEK[day.weekday()]
    if plan is None:
        return None
    sport, name, minutes, inten = plan
    r = _rng("act", day.isoformat())
    if day.weekday() == 6 and day.isocalendar()[1] % 2 == 0:
        sport, name, minutes, inten = "multi_sport", "Brick: 90 min bike + 20 min run", 115, 0.55
    dur = minutes * 60 * r.uniform(0.93, 1.07)
    start = datetime(day.year, day.month, day.day, 6 if day.weekday() < 5 else 7, r.randint(0, 40))
    a: dict[str, Any] = {
        "activityId": 21_000_000_000 + idx,
        "activityName": name,
        "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": sport},
        "duration": round(dur, 1),
        "movingDuration": round(dur * 0.97, 1),
        "averageHR": round(118 + 45 * inten + r.uniform(-4, 4)),
        "maxHR": round(150 + 30 * inten + r.uniform(-3, 3)),
        "aerobicTrainingEffect": round(2.2 + 1.8 * inten + r.uniform(-0.2, 0.2), 1),
        "anaerobicTrainingEffect": round(max(0.0, 3.2 * inten - 1 + r.uniform(-0.3, 0.3)), 1),
        "trainingEffectLabel": "VO2MAX" if inten > 0.75 else "TEMPO" if inten > 0.55 else "BASE",
        "activityTrainingLoad": round(dur / 60 * (0.6 + 1.6 * inten) * r.uniform(0.9, 1.1)),
    }
    if sport == "lap_swimming":
        speed = 100 / r.uniform(110, 122)  # ~1:50-2:02 per 100 m
        a.update(distance=round(speed * dur / 25) * 25, averageSpeed=round(speed, 3),
                 poolLength=25, unitOfPoolLength={"unitKey": "meter"},
                 strokes=int(dur / 60 * 28), averageSwolf=round(r.uniform(38, 42)),
                 activeLengths=int(speed * dur / 25),
                 averageSwimCadenceInStrokesPerMinute=round(r.uniform(26, 30)))
    elif sport == "running":
        pace = 5.6 - 1.1 * inten + r.uniform(-0.1, 0.1)  # min/km
        speed = 1000 / (pace * 60)
        a.update(distance=round(speed * dur), averageSpeed=round(speed, 3),
                 elevationGain=round(r.uniform(20, 140)),
                 averageRunningCadenceInStepsPerMinute=round(r.uniform(166, 176)))
    elif sport in ("cycling", "indoor_cycling"):
        speed = (27 + 6 * inten) / 3.6 if sport == "cycling" else 0
        power = 150 + 110 * inten + r.uniform(-8, 8)
        a.update(distance=round(speed * dur) if speed else None,
                 averageSpeed=round(speed, 3) if speed else None,
                 elevationGain=round(r.uniform(300, 1100)) if speed else 0,
                 avgPower=round(power), normPower=round(power * 1.06),
                 maxPower=round(power * 2.6), averageBikingCadenceInRevPerMinute=round(r.uniform(84, 92)))
    else:  # brick parent
        a.update(distance=46000, averageSpeed=6.9, isMultiSportParent=True,
                 elevationGain=round(r.uniform(300, 700)))
    return {k: v for k, v in a.items() if v is not None}


_SIMULATED: list[dict[str, Any]] = []  # dev "simulate workout" additions, newest last


def simulate_activity() -> dict[str, Any]:
    """Add a just-finished tempo run (dev only), so the poller sees a new activity."""
    now = datetime.now()
    r = _rng("sim", len(_SIMULATED), now.isoformat())
    dur = r.uniform(40, 55) * 60
    pace = r.uniform(4.6, 4.9)
    act = {
        "activityId": 21_000_100_000 + len(_SIMULATED),
        "activityName": "Tempo run 3x10 min",
        "startTimeLocal": (now - timedelta(seconds=dur)).strftime("%Y-%m-%d %H:%M:%S"),
        "activityType": {"typeKey": "running"},
        "duration": round(dur, 1), "movingDuration": round(dur * 0.98, 1),
        "distance": round(1000 / (pace * 60) * dur), "averageSpeed": round(1000 / (pace * 60), 3),
        "averageHR": round(r.uniform(152, 160)), "maxHR": round(r.uniform(170, 178)),
        "elevationGain": round(r.uniform(30, 90)),
        "averageRunningCadenceInStepsPerMinute": round(r.uniform(172, 180)),
        "aerobicTrainingEffect": 3.7, "anaerobicTrainingEffect": 1.4,
        "trainingEffectLabel": "THRESHOLD", "activityTrainingLoad": round(r.uniform(110, 140)),
    }
    _SIMULATED.append(act)
    return act


def _activities(today: date) -> list[dict[str, Any]]:
    out = []
    for i in range(DAYS):
        day = today - timedelta(days=DAYS - 1 - i)
        a = _activity(day, i)
        if a:
            out.append(a)
    return list(reversed(out + _SIMULATED))  # newest first, like Garmin


def _day(cdate: str) -> date:
    try:
        return date.fromisoformat(cdate)
    except ValueError:
        raise GarminError("Invalid arguments: dates must be YYYY-MM-DD") from None


def _sleep(d: date) -> dict[str, Any]:
    r = _rng("sleep", d)
    total = int(r.uniform(6.3, 8.1) * 3600)
    deep, rem = int(total * r.uniform(0.14, 0.22)), int(total * r.uniform(0.18, 0.25))
    awake = int(r.uniform(10, 40) * 60)
    return {"dailySleepDTO": {
        "calendarDate": d.isoformat(), "sleepTimeSeconds": total, "deepSleepSeconds": deep,
        "remSleepSeconds": rem, "lightSleepSeconds": total - deep - rem, "awakeSleepSeconds": awake,
        "averageRespirationValue": round(r.uniform(13, 15), 1),
        "sleepScores": {"overall": {"value": round(55 + total / 3600 * 4 + r.uniform(-6, 6))}},
    }, "restingHeartRate": round(r.uniform(46, 52)), "avgOvernightHrv": round(r.uniform(52, 68))}


def _hrv(d: date) -> dict[str, Any]:
    r = _rng("hrv", d)
    last = round(r.uniform(50, 70))
    return {"hrvSummary": {
        "calendarDate": d.isoformat(), "weeklyAvg": round(r.uniform(56, 62)), "lastNightAvg": last,
        "lastNight5MinHigh": last + round(r.uniform(10, 20)),
        "baseline": {"lowUpper": 50, "balancedLow": 54, "balancedUpper": 68},
        "status": "BALANCED" if last >= 54 else "UNBALANCED",
    }}


def _readiness(d: date) -> list[dict[str, Any]]:
    r = _rng("ready", d)
    score = round(r.uniform(42, 88))
    return [{"calendarDate": d.isoformat(), "score": score,
             "level": "HIGH" if score > 75 else "MODERATE" if score > 50 else "LOW",
             "sleepScore": _sleep(d)["dailySleepDTO"]["sleepScores"]["overall"]["value"],
             "recoveryTime": round(r.uniform(4, 30)), "acuteLoad": round(r.uniform(420, 620)),
             "hrvWeeklyAverage": _hrv(d)["hrvSummary"]["weeklyAvg"]}]


def _summary(d: date) -> dict[str, Any]:
    r = _rng("sum", d)
    return {"calendarDate": d.isoformat(), "totalSteps": round(r.uniform(7000, 16000)),
            "restingHeartRate": _sleep(d)["restingHeartRate"],
            "bodyBatteryHighestValue": round(r.uniform(70, 98)),
            "bodyBatteryLowestValue": round(r.uniform(10, 35)),
            "averageStressLevel": round(r.uniform(22, 38)), "activeKilocalories": round(r.uniform(500, 1600))}


def fake_answer(fn_name: str, kwargs: dict[str, Any], today: date | None = None) -> Any:
    today = today or date.today()
    acts = _activities(today)
    by_id = {str(a["activityId"]): a for a in acts}
    if fn_name == "get_full_name":
        return "Fake Athlete"
    if fn_name == "get_activities":
        start, limit = int(kwargs.get("start", 0)), int(kwargs.get("limit", 20))
        return acts[start:start + limit]
    if fn_name == "get_last_activity":
        return acts[0] if acts else None
    if fn_name == "get_activities_by_date":
        lo = _day(kwargs["startdate"])
        hi = _day(kwargs["enddate"]) if kwargs.get("enddate") else today
        want = kwargs.get("activitytype")
        out = [a for a in acts if lo <= date.fromisoformat(a["startTimeLocal"][:10]) <= hi]
        if want:
            out = [a for a in out if want in a["activityType"]["typeKey"]
                   or (want == "cycling" and a["activityType"]["typeKey"] == "indoor_cycling")]
        return out
    if fn_name in ("get_activity", "get_activity_splits"):
        a = by_id.get(str(kwargs.get("activity_id")))
        if a is None:
            raise GarminError("Garmin says the requested resource does not exist.")
        if fn_name == "get_activity":
            return {"activityId": a["activityId"], "activityName": a["activityName"],
                    "summaryDTO": {k: v for k, v in a.items() if k not in ("activityType",)}}
        n = max(1, int(a.get("distance", 5000) // 1000))
        return {"activityId": a["activityId"], "lapDTOs": [
            {"lapIndex": i + 1, "distance": 1000.0, "duration": round(a["duration"] / n, 1),
             "averageHR": a["averageHR"] + (i % 3) - 1, "averageSpeed": a.get("averageSpeed")}
            for i in range(min(n, 30))]}
    if fn_name == "get_sleep_data":
        return _sleep(_day(kwargs["cdate"]))
    if fn_name == "get_hrv_data":
        return _hrv(_day(kwargs["cdate"]))
    if fn_name == "get_training_readiness":
        return _readiness(_day(kwargs["cdate"]))
    if fn_name == "get_morning_training_readiness":
        return _readiness(_day(kwargs["cdate"]))[0]
    if fn_name in ("get_user_summary", "get_stats"):
        return _summary(_day(kwargs["cdate"]))
    if fn_name == "get_rhr_day":
        d = _day(kwargs["cdate"])
        return {"calendarDate": d.isoformat(), "restingHeartRate": _sleep(d)["restingHeartRate"]}
    if fn_name == "get_body_battery":
        lo = _day(kwargs["startdate"])
        hi = _day(kwargs["enddate"]) if kwargs.get("enddate") else lo
        days = [(lo + timedelta(days=i)) for i in range((hi - lo).days + 1)][:31]
        return [{"date": d.isoformat(), "charged": round(_rng("bb", d).uniform(40, 80)),
                 "drained": round(_rng("bbd", d).uniform(40, 85))} for d in days]
    if fn_name in ("get_training_status", "get_daily_training_status"):
        d = _day(kwargs["cdate"])
        return {"calendarDate": d.isoformat(), "trainingStatus": "PRODUCTIVE",
                "acuteTrainingLoad": 512, "chronicTrainingLoad": 468, "loadRatio": 1.09,
                "vo2MaxRunning": 52.0, "vo2MaxCycling": 54.0}
    if fn_name == "get_race_predictions":
        return {"time5K": 1290, "time10K": 2710, "timeHalfMarathon": 6020, "timeMarathon": 12780}
    raise GarminError(f"Fake Garmin has no data for {fn_name}.")


class FakeSession(GarminSession):
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    def call(self, fn: Callable[..., Any], kwargs: dict[str, Any], *, retry: bool) -> Any:
        return fake_answer(getattr(fn, "__name__", ""), kwargs)


class FakeGarminProvider(GarminProvider):
    """Scripted logins: ``mfa@...`` asks for MFA (code 123456), ``wrong@...`` is
    rejected, ``429@...`` is rate limited, ``cf@...`` is a Cloudflare block;
    anything else succeeds. Each login call takes ``delay`` seconds."""

    def __init__(self, token_store: Any, delay: float = 2.0) -> None:
        self._tokens = token_store
        self._delay = delay

    def get_client(self, user_id: str) -> FakeSession:
        if not self._tokens.load(user_id):
            raise ReauthRequired(user_id, "no Garmin tokens stored")
        return FakeSession(user_id)

    def login_start(self, user_id: str, email: str, password: str) -> LoginResult:
        time.sleep(self._delay)
        local = email.split("@")[0].lower()
        if local == "wrong":
            from garminconnect import GarminConnectAuthenticationError
            raise GarminConnectAuthenticationError("fake: bad credentials")
        if local == "429":
            raise RateLimited("fake: 429")
        if local == "cf":
            raise CloudflareBlocked()
        if local == "mfa":
            return LoginResult(LoginStatus.NEEDS_MFA, LoginHandle(user_id, "fake"))
        self._tokens.save(user_id, '{"fake": true}')
        return LoginResult(LoginStatus.DONE)

    def login_complete(self, handle: LoginHandle, mfa_code: str) -> None:
        time.sleep(self._delay)
        if mfa_code.strip() != "123456":
            from garminconnect import GarminConnectAuthenticationError
            raise GarminConnectAuthenticationError("fake: bad mfa")
        self._tokens.save(handle.user_id, '{"fake": true}')
