"""``training_history``: months of Garmin activity summarized in one tool call.

Built for goal setup, so the coach starts from what the athlete has actually been doing
(weekly volume by sport, consistency, longest sessions, races) instead of asking. Pure
aggregation over compact activities; the Garmin reads are ordinary cached reads.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .weekplan import _seconds, sport_of

KM_MI = 0.621371
M_YD = 1.09361
MAX_EVENTS = 15


def _dist(sport: str, km: float, imperial: bool) -> tuple[float, str]:
    if sport == "swim":
        return (round(km * 1000 * M_YD), "yd") if imperial else (round(km * 1000), "m")
    return (round(km * KM_MI, 1), "mi") if imperial else (round(km, 1), "km")


def summarize(acts: list[dict[str, Any]], start: date, end: date, *, imperial: bool,
              older_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """``acts``: compact activities in [start, end]. ``older_events``: multisport activities
    from before ``start`` (past triathlons), listed with the events."""
    parents = {a.get("activity_id") for a in acts if (a.get("multisport") or {}).get("is_parent")}
    rows = []
    for a in acts:
        if (a.get("multisport") or {}).get("parent_id") in parents:
            continue  # legs of a multisport activity; the parent stands for them
        day = str(a.get("date") or "")[:10]
        try:
            d = date.fromisoformat(day)
        except ValueError:
            continue
        rows.append({"date": d, "sport": sport_of(a.get("sport")), "name": a.get("name") or "",
                     "sec": _seconds(a.get("duration")), "km": a.get("distance_km") or 0.0,
                     "event": a.get("event"), "raw_sport": a.get("sport")})

    # Weekly buckets, Monday-start, oldest first, including empty weeks.
    first = start - timedelta(days=start.weekday())
    weeks: dict[date, dict[str, float]] = {}
    w = first
    while w <= end:
        weeks[w] = defaultdict(float)
        w += timedelta(days=7)
    for r in rows:
        weeks[r["date"] - timedelta(days=r["date"].weekday())][r["sport"]] += r["sec"] / 3600
    weekly = [{"week_of": k.isoformat(), "hours": round(sum(v.values()), 1),
               "by_sport": {s: round(h, 1) for s, h in sorted(v.items()) if h >= 0.05}}
              for k, v in weeks.items()]

    by_sport: dict[str, dict[str, Any]] = {}
    for sport in sorted({r["sport"] for r in rows}):
        rs = [r for r in rows if r["sport"] == sport]
        longest = max(rs, key=lambda r: r["sec"])
        total_km = sum(r["km"] for r in rs)
        entry: dict[str, Any] = {"sessions": len(rs), "hours": round(sum(r["sec"] for r in rs) / 3600, 1)}
        if total_km and sport not in ("strength", "other"):
            dist, unit = _dist(sport, total_km, imperial)
            entry[f"distance_{unit}"] = dist
        entry["longest"] = {"date": longest["date"].isoformat(), "minutes": round(longest["sec"] / 60)}
        if longest["km"]:
            dist, unit = _dist(sport, longest["km"], imperial)
            entry["longest"][f"distance_{unit}"] = dist
        by_sport[sport] = entry

    hours = [x["hours"] for x in weekly]
    days = sorted({r["date"] for r in rows})
    gaps = [(b - a).days for a, b in zip(days, days[1:])]
    if days:
        gaps.append((end - days[-1]).days)
    consistency = {
        "active_weeks": sum(h > 0 for h in hours), "weeks": len(hours),
        "avg_hours_per_week": round(sum(hours) / len(hours), 1) if hours else 0,
        "last_4_weeks_avg_hours": round(sum(hours[-4:]) / min(4, len(hours)), 1) if hours else 0,
        "longest_break_days": max(gaps) if gaps else (end - start).days,
        "last_activity": days[-1].isoformat() if days else None,
    }

    def event_row(r: dict[str, Any]) -> dict[str, Any]:
        out = {"date": r["date"].isoformat(), "name": r["name"], "sport": r["raw_sport"],
               "duration": f"{r['sec'] // 3600}:{r['sec'] % 3600 // 60:02d}:{r['sec'] % 60:02d}"}
        if r["km"]:
            dist, unit = _dist(r["sport"], r["km"], imperial)
            out[f"distance_{unit}"] = dist
        if r["event"]:
            out["tagged"] = r["event"]
        return out

    events = [r for r in rows if r["sport"] == "brick" or r["event"] == "race"]
    older = []
    for a in older_events or []:
        if (a.get("multisport") or {}).get("parent_id"):
            continue
        try:
            d = date.fromisoformat(str(a.get("date") or "")[:10])
        except ValueError:
            continue
        older.append({"date": d, "sport": "brick", "name": a.get("name") or "",
                      "sec": _seconds(a.get("duration")), "km": a.get("distance_km") or 0.0,
                      "event": a.get("event"), "raw_sport": a.get("sport")})
    events = sorted(events + older, key=lambda r: r["date"], reverse=True)[:MAX_EVENTS]

    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "units": "imperial (swim in yards)" if imperial else "metric",
        "consistency": consistency,
        "by_sport": by_sport,
        "weekly": weekly,
        "races_and_multisport": [event_row(r) for r in events],
        "note": "Durations are moving/elapsed time from Garmin. Weekly buckets start on Monday.",
    }
