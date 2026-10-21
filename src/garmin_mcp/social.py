"""Read-only methods for the user's Garmin connections (not in garminconnect).

These are registry extensions (``Registry(extensions=METHODS)``, on only when
GARMIN_SOCIAL_ENABLED is set) with the same calling convention as the library's
methods: ``fn(client, **kwargs)`` where ``client`` is a ``garminconnect.Garmin``.
Each builds its path from a fixed template and calls ``client.connectapi``,
which stays blocked for direct ``execute`` calls.

A ``connection`` argument is never taken from the caller as-is: ``GarminService``
first resolves it against the caller's own ``get_connections`` list and passes
the matched ``displayName``. ``_display_name`` re-checks and URL-encodes it here
anyway, so a hostile display name can't add path segments.

Endpoints and quirks come from docs/plans/connections.md (Phase 0): pagination
is 1-based, and ``activities/{displayName}`` ignores date parameters.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import quote

from .tools import compact_activity

_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
CONNECTIONS_PAGE = 100
MAX_CONNECTION_PAGES = 5
MAX_ACTIVITY_LIMIT = 100
MAX_LEADERBOARD_DAYS = 31
STATS_PERIODS = ("all", "monthly", "recent")
LEADERBOARD_STEPS = 29
PROFILE_KEYS = (
    "fullName", "location", "bio", "motivation", "primaryActivity",
    "favoriteActivityTypes", "favoriteCyclingActivityTypes",
    "runningTrainingSpeed", "cyclingTrainingSpeed", "swimmingTrainingSpeed",
    "cyclingMaxAvgPower", "cyclingClassification",
    "profileVisibility", "activityStartVisibility", "activityMapVisibility",
    "activityHeartRateVisibility", "activityPowerVisibility", "courseVisibility",
    "badgeVisibility",
)


# ------------------------------------------------------------- validation --
def _display_name(value: Any) -> str:
    if not isinstance(value, str) or not _DISPLAY_NAME_RE.match(value):
        raise ValueError("connection display name has an unsupported format")
    return quote(value, safe="")


def _own_display_name(client: Any) -> str:
    return _display_name(getattr(client, "display_name", None))


def _int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} must be an integer from {lo} to {hi}")
    return value


def _date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be YYYY-MM-DD") from None


def _without(d: Any, *keys: str) -> Any:
    return {k: v for k, v in d.items() if k not in keys} if isinstance(d, dict) else d


def _type_key(v: Any) -> Any:
    return v.get("typeKey") if isinstance(v, dict) else v


# ---------------------------------------------------------------- fetches --
def _fetch_connections(client: Any) -> list[dict[str, Any]]:
    path = f"/connection-service/connection/v2/connections/pagination/{_own_display_name(client)}"
    out: list[dict[str, Any]] = []
    for page in range(MAX_CONNECTION_PAGES):
        body = client.connectapi(path, params={
            "start": str(page * CONNECTIONS_PAGE + 1), "limit": str(CONNECTIONS_PAGE),
            "displayMutedStatus": "false",
        })
        rows = body.get("userConnections") if isinstance(body, dict) else None
        rows = [r for r in rows or [] if isinstance(r, dict)]
        out.extend(rows)
        if len(rows) < CONNECTIONS_PAGE:
            break
    return out


# ---------------------------------------------------------------- methods --
def get_connections(client: Any) -> dict[str, Any]:
    """List your Garmin Connect connections (friends): display_name, full_name, location.

    Use a full_name (or a unique part of it) or display_name as the connection
    argument of the get_connection_* methods.
    """
    rows = _fetch_connections(client)
    conns = [
        {k: v for k, v in {
            "display_name": r.get("displayName"),
            "full_name": r.get("fullName"),
            "location": r.get("location"),
            "user_id": r.get("userId"),
        }.items() if v not in (None, "")}
        for r in rows if r.get("displayName")
    ]
    return {"count": len(conns), "connections": conns}


def get_connection_profile(client: Any, connection: str) -> dict[str, Any]:
    """Return a connection's (friend's) social profile: favorite sports, training speeds, visibility settings.

    connection: a full name, unique part of one, or display_name from get_connections.
    """
    body = client.connectapi(f"/userprofile-service/socialProfile/{_display_name(connection)}")
    body = body if isinstance(body, dict) else {}
    return {k: body[k] for k in PROFILE_KEYS if body.get(k) not in (None, "", [])}


def get_connection_activities(client: Any, connection: str, start: int = 0,
                              limit: int = 20) -> dict[str, Any]:
    """Return a connection's (friend's) recent activities, newest first, in compact form.

    connection: a full name, unique part of one, or display_name from get_connections.
    start is a 0-based offset; limit is at most 100. Garmin only returns
    activities the connection shares with you. Use an activity_id with the usual
    activity methods (get_activity, get_activity_splits, ...) for details.
    """
    start = _int(start, "start", 0, 10_000)
    limit = _int(limit, "limit", 1, MAX_ACTIVITY_LIMIT)
    body = client.connectapi(
        f"/activitylist-service/activities/{_display_name(connection)}",
        params={"start": str(start + 1), "limit": str(limit)},  # Garmin is 1-based
    )
    rows = body.get("activityList") if isinstance(body, dict) else None
    acts = []
    for a in rows or []:
        if isinstance(a, dict):
            c = compact_activity(a)
            if _type_key(a.get("privacy")):
                c["privacy"] = _type_key(a.get("privacy"))
            acts.append(c)
    return {"start": start, "count": len(acts), "activities": acts}


def get_connection_stats(client: Any, connection: str, period: str = "all") -> dict[str, Any]:
    """Return a connection's (friend's) activity totals per sport: count, distance, duration, calories, elevation.

    connection: a full name, unique part of one, or display_name from get_connections.
    period: "all" (all-time totals plus lifetime steps/distance), "monthly"
    (per month), or "recent" (Garmin's recent-days window). Values are in
    Garmin's units, unconverted.
    """
    if period not in STATS_PERIODS:
        raise ValueError(f"period must be one of {list(STATS_PERIODS)}")
    dn = _display_name(connection)
    path, params = {
        "all": (f"/userstats-service/statistics/{dn}", None),
        "monthly": (f"/userstats-service/statistics/monthly/{dn}", {"ByParentType": "true"}),
        "recent": (f"/userstats-service/statistics/previousDays/{dn}", None),
    }[period]
    body = client.connectapi(path, params=params)
    rows = body.get("userMetrics") if isinstance(body, dict) else None
    metrics = [
        {**_without(m, "userProfileId"),
         "activityType": _type_key(m.get("activityType")),
         "parentActivityType": _type_key(m.get("parentActivityType"))}
        for m in rows or [] if isinstance(m, dict)
    ]
    out: dict[str, Any] = {"period": period, "metrics": metrics}
    if period == "all":
        totals = client.connectapi(f"/usersummary-service/stats/connectLifetimeTotals/{dn}")
        out["lifetime"] = _without(totals, "userProfileId")
    return out


def get_connection_personal_records(client: Any, connection: str) -> dict[str, Any]:
    """Return a connection's (friend's) personal records (fastest 5K, longest ride, ...).

    connection: a full name, unique part of one, or display_name from get_connections.
    value is in Garmin's units for the record type (a time or a distance).
    """
    dn = _display_name(connection)
    prs = client.connectapi(f"/personalrecord-service/personalrecord/prs/{dn}",
                            params={"includeHistory": "false"})
    types = client.connectapi(f"/personalrecord-service/personalrecordtype/prtypes/{dn}")
    names = {
        t.get("id"): str(t.get("key") or "").removeprefix("pr.label.")
        for t in types or [] if isinstance(t, dict)
    }
    records = [
        {k: v for k, v in {
            "type": names.get(p.get("typeId")) or p.get("typeId"),
            "value": p.get("value"),
            "date": p.get("actStartDateTimeInGMTFormatted") or p.get("prStartTimeGmtFormatted"),
            "activity_id": p.get("activityId"),
            "activity_name": p.get("activityName"),
            "activity_type": p.get("activityType"),
        }.items() if v not in (None, "")}
        for p in prs or [] if isinstance(p, dict)
    ]
    return {"count": len(records), "records": records}


def get_connections_leaderboard(client: Any, startdate: str, enddate: str) -> dict[str, Any]:
    """Return the steps leaderboard for you and your connections (friends) over a date range.

    startdate/enddate are YYYY-MM-DD, at most 31 days apart.
    """
    start, end = _date(startdate, "startdate"), _date(enddate, "enddate")
    if not 0 <= (end - start).days < MAX_LEADERBOARD_DAYS:
        raise ValueError(f"enddate must be on or after startdate, within {MAX_LEADERBOARD_DAYS} days")
    body = client.connectapi("/usersummary-service/stats/wellness/leaderboard/FOLLOWING", params={
        "start": "1", "limit": "100", "metricId": str(LEADERBOARD_STEPS),
        "startDate": start.isoformat(), "endDate": end.isoformat(),
    })
    body = body if isinstance(body, dict) else {}
    me = body.get("userProfileId")
    names = {r.get("userId"): r.get("fullName") for r in _fetch_connections(client)}
    entries = []
    for s in body.get("userStats") or []:
        if not isinstance(s, dict):
            continue
        pk = s.get("userProfilePk")
        entries.append({k: v for k, v in {
            "rank": s.get("rank"),
            "name": "you" if pk == me else names.get(pk) or "unknown",
            "total": None if s.get("allPrivate") else s.get("totalValue"),
            "private": s.get("allPrivate") or None,
        }.items() if v is not None})
    return {"metric": body.get("wellnessLeaderBoardMetricType") or "TOTAL_STEPS",
            "from": start.isoformat(), "to": end.isoformat(),
            "your_rank": body.get("userRank"), "entries": entries}


METHODS = (
    get_connections,
    get_connection_profile,
    get_connection_activities,
    get_connection_stats,
    get_connection_personal_records,
    get_connections_leaderboard,
)
