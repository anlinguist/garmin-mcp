"""Tool implementations, independent of the MCP transport (easy to unit test)."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from .logs import log_event
from .providers import GarminError, GarminProvider, RateLimited, ReauthRequired
from .registry import Access, ExecuteDenied, Registry
from .resilience import RateLimiter, TTLCache
from .shaping import shape

logger = logging.getLogger(__name__)

SPORT_ALIASES = {
    "run": "running", "running": "running",
    "bike": "cycling", "ride": "cycling", "cycling": "cycling",
    "swim": "swimming", "swimming": "swimming",
    "brick": "multi_sport", "multisport": "multi_sport", "multi_sport": "multi_sport",
    "triathlon": "multi_sport",
    "strength": "fitness_equipment", "fitness_equipment": "fitness_equipment",
    "walk": "walking", "walking": "walking", "hike": "hiking", "hiking": "hiking",
    "other": "other",
}
MAX_DAYS = 90


class GarminService:
    def __init__(
        self,
        provider: GarminProvider,
        registry: Registry,
        *,
        allow_writes: bool,
        cache: TTLCache,
        limiter: RateLimiter,
        max_response_bytes: int,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.allow_writes = allow_writes
        self.cache = cache
        self.limiter = limiter
        self.max_bytes = max_response_bytes

    # ---------------------------------------------------------------- search --
    def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        hits = self.registry.search(query or "", limit=limit)
        return {
            "writes_enabled": self.allow_writes,
            "results": [m.public(self.allow_writes) for m in hits],
            "hint": "Call execute with {method, args}. Dates are 'YYYY-MM-DD'.",
        }

    # --------------------------------------------------------------- execute --
    def _upstream(self, user_id: str, fn: Any, kwargs: dict[str, Any], *, read: bool) -> Any:
        if not self.limiter.allow(user_id):
            raise RateLimited("Per-user rate limit reached for this server; wait a minute.")
        session = self.provider.get_client(user_id)
        try:
            return session.call(fn, kwargs, retry=read)
        except ReauthRequired:
            self.provider.invalidate(user_id)
            self.cache.clear_user(user_id)
            raise

    def _cached_call(self, user_id: str, name: str, fn: Any, kwargs: dict[str, Any]) -> Any:
        key = TTLCache.key(user_id, name, kwargs)
        hit, value = self.cache.get(key)
        if hit:
            log_event(logger, logging.DEBUG, "cache_hit", user_id=user_id, method=name)
            return value
        value = self._upstream(user_id, fn, kwargs, read=True)
        self.cache.set(key, value)
        return value

    def execute(self, user_id: str, method: str, args: dict[str, Any] | None,
                full: bool = False) -> dict[str, Any]:
        try:
            info, kwargs = self.registry.resolve(method, args, self.allow_writes)
        except ExecuteDenied as e:
            log_event(logger, logging.INFO, "execute_denied", user_id=user_id,
                      method=str(method)[:80])
            raise GarminError(str(e)) from None

        log_event(logger, logging.INFO, "execute", user_id=user_id, method=info.name,
                  access=info.access.value, arg_names=sorted(kwargs))
        if info.access is Access.READ:
            result = self._cached_call(user_id, info.name, info.fn, kwargs)
        else:
            result = self._upstream(user_id, info.fn, kwargs, read=False)
            self.cache.clear_user(user_id)
        if isinstance(result, bytes | bytearray):  # defensive; binary methods are blocked
            result = {"_omitted": f"{len(result)} bytes of binary data"}
        return {"method": info.name, **shape(result, full=full, max_bytes=self.max_bytes)}

    # ---------------------------------------------------- recent_activities --
    def recent_activities(self, user_id: str, days: int = 7,
                          sport: str | None = None) -> dict[str, Any]:
        days = max(1, min(int(days), MAX_DAYS))
        activity_type = None
        if sport:
            activity_type = SPORT_ALIASES.get(sport.strip().lower())
            if activity_type is None:
                raise GarminError(f"Unknown sport {sport!r}; try one of {sorted(set(SPORT_ALIASES))}")
        end = date.today()
        start = end - timedelta(days=days - 1)
        info = self.registry.get("get_activities_by_date")
        if info is None or info.access is not Access.READ:
            raise GarminError("get_activities_by_date unavailable in this garminconnect version")
        kwargs: dict[str, Any] = {"startdate": start.isoformat(), "enddate": end.isoformat()}
        if activity_type:
            kwargs["activitytype"] = activity_type
        raw = self._cached_call(user_id, info.name, info.fn, kwargs) or []
        acts = [compact_activity(a) for a in raw if isinstance(a, dict)]
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "sport_filter": activity_type, "count": len(acts), "activities": acts,
        }


# ------------------------------------------------------------ compaction ---
def _round(v: Any, n: int = 1) -> Any:
    return round(v, n) if isinstance(v, int | float) else None


def _fmt_duration(seconds: Any) -> str | None:
    if not isinstance(seconds, int | float):
        return None
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _pace(speed_mps: Any, per_m: float) -> str | None:
    if not isinstance(speed_mps, int | float) or speed_mps <= 0:
        return None
    return _fmt_duration(per_m / speed_mps)


def compact_activity(a: dict[str, Any]) -> dict[str, Any]:
    atype = a.get("activityType") or {}
    type_key = atype.get("typeKey") if isinstance(atype, dict) else None
    speed = a.get("averageSpeed")
    key = (type_key or "").lower()
    out: dict[str, Any] = {
        "activity_id": a.get("activityId"),
        "date": a.get("startTimeLocal"),
        "name": a.get("activityName"),
        "sport": type_key,
        "duration": _fmt_duration(a.get("duration")),
        "moving_duration": _fmt_duration(a.get("movingDuration")),
        "distance_km": _round((a.get("distance") or 0) / 1000, 2) if a.get("distance") else None,
        "avg_hr": _round(a.get("averageHR"), 0),
        "max_hr": _round(a.get("maxHR"), 0),
        "elevation_gain_m": _round(a.get("elevationGain"), 0),
        "avg_power_w": _round(a.get("avgPower"), 0),
        "max_power_w": _round(a.get("maxPower"), 0),
        "norm_power_w": _round(a.get("normPower"), 0),
        "aerobic_te": _round(a.get("aerobicTrainingEffect")),
        "anaerobic_te": _round(a.get("anaerobicTrainingEffect")),
        "training_effect_label": a.get("trainingEffectLabel"),
        "training_load": _round(a.get("activityTrainingLoad"), 0),
    }
    if "swim" in key:
        out["avg_pace_per_100m"] = _pace(speed, 100)
        pool = a.get("poolLength")
        unit = (a.get("unitOfPoolLength") or {}).get("unitKey") if isinstance(
            a.get("unitOfPoolLength"), dict) else None
        out["pool_length"] = f"{_round(pool)} {unit or ''}".strip() if pool else None
        out["strokes"] = a.get("strokes")
        out["avg_swolf"] = _round(a.get("averageSwolf"), 0)
        out["active_lengths"] = a.get("activeLengths")
        out["avg_stroke_rate"] = _round(a.get("averageSwimCadenceInStrokesPerMinute"), 0)
    elif any(k in key for k in ("run", "walk", "hik")):
        out["avg_pace_per_km"] = _pace(speed, 1000)
        out["avg_cadence"] = _round(a.get("averageRunningCadenceInStepsPerMinute"), 0)
    else:
        out["avg_speed_kmh"] = _round(speed * 3.6) if isinstance(speed, int | float) else None
        out["avg_cadence"] = _round(a.get("averageBikingCadenceInRevPerMinute"), 0)
    if a.get("isMultiSportParent") or a.get("parentId") or key == "multi_sport":
        out["multisport"] = {"is_parent": bool(a.get("isMultiSportParent")),
                             "parent_id": a.get("parentId")}
    return {k: v for k, v in out.items() if v not in (None, "", {})}
