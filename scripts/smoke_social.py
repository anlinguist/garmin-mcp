#!/usr/bin/env python3
"""Phase 0 of docs/plans/connections.md: do the web app's connection endpoints
work on connectapi.garmin.com with the library's bearer token? NOT run in CI.

Answers, for one connection:
  * which endpoints return 200 through ``client.connectapi`` (vs 401/403/404)
  * whether list pagination is 0- or 1-based
  * whether ``activities/{displayName}`` honours startDate/endDate
  * whether the existing registry methods (get_activity, ...) read a
    connection's activity, and whether HR is present

Careful by design, so a run can't get the VM's IP flagged:
  * about 14 GETs in total, small page sizes, one at a time with a pause
    between them (--delay, default 3s, jittered)
  * no retries anywhere (registry methods are called directly, not through
    GarminService, whose reads retry with backoff)
  * stops at the first 401, 403 or 429, or any Cloudflare marker
  * token resume only, never a credential login: if the stored tokens are
    rejected it exits and you re-run garmin-mcp-login as usual

Prereq: garmin-mcp-login has been run for the user on this machine.
Usage (on the VM):
    sudo -u garmin-mcp bash -c 'set -a; . /etc/garmin-mcp/env; set +a; \
        /opt/garmin-mcp/current/.venv/bin/python /opt/garmin-mcp/current/scripts/smoke_social.py --user andrew'

    --connection picks a connection by display name or full-name prefix
    (default: the first one). It is never echoed.

Prints only statuses, counts, key names, enum values and yes/no answers, never
names, display names or payloads. It shares the token store with the running
server (like smoke_live.py); run it shortly after a server call so the access
token is fresh and neither process needs to refresh it.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from garmin_mcp.config import Settings
from garmin_mcp.logs import setup_logging
from garmin_mcp.providers import GarminError
from garmin_mcp.providers.unofficial import UnofficialProvider, _probe, _status_of
from garmin_mcp.registry import Access, Registry
from garmin_mcp.secrets import get_secret
from garmin_mcp.tokens import EncryptedFileTokenStore

CONNECTIONS = "/connection-service/connection/v2/connections/pagination/{dn}"
LEADERBOARD = "/usersummary-service/stats/wellness/leaderboard/FOLLOWING"
FRIEND_PATHS = [
    ("socialProfile", "/userprofile-service/socialProfile/{dn}", None),
    ("stats all", "/userstats-service/statistics/{dn}", None),
    ("stats monthly", "/userstats-service/statistics/monthly/{dn}", {"ByParentType": "true"}),
    ("lifetime totals", "/usersummary-service/stats/connectLifetimeTotals/{dn}", None),
    ("PRs", "/personalrecord-service/personalrecord/prs/{dn}", {"includeHistory": "false"}),
    ("PR types", "/personalrecord-service/personalrecordtype/prtypes/{dn}", None),
]
ACTIVITIES = "/activitylist-service/activities/{dn}"
REGISTRY_METHODS = ("get_activity", "get_activity_splits")
VISIBILITY_KEYS = ("profileVisibility", "activityHeartRateVisibility",
                   "activityPowerVisibility", "activityMapVisibility")
STOP_STATUSES = {401, 403, 429}


class Stop(Exception):
    """A response that means: stop making requests now."""


class Prober:
    def __init__(self, session: Any, delay: float) -> None:
        self.session = session
        self.delay = delay
        self.failures = 0
        self.requests = 0

    def _pause(self) -> None:
        if self.requests:
            time.sleep(self.delay * (0.75 + random.random() / 2))
        self.requests += 1

    def _run(self, label: str, call: Any) -> Any:
        """One request, no retries. Prints a status line; raises Stop on a block signal."""
        def fn(client: Any) -> tuple[bool, Any, int | None, bool]:
            try:
                return True, call(client), _probe.status, _probe.cloudflare
            except Exception as exc:  # report, don't let session.call translate it
                return (False, type(exc).__name__, _probe.status or _status_of(exc),
                        _probe.cloudflare)

        self._pause()
        ok, body, status, cloudflare = self.session.call(fn, {}, retry=False)
        if cloudflare or (not ok and status in STOP_STATUSES):
            print(f"   STOP {label:<28} status={status} cloudflare={cloudflare}")
            raise Stop(f"{label}: status={status} cloudflare={cloudflare}")
        if not ok:
            self.failures += 1
            print(f"   FAIL {label:<28} {body} status={status}")
            return None
        print(f"   ok   {label:<28} {describe(body)}")
        return body

    def get(self, label: str, path: str, params: dict[str, str] | None = None) -> Any:
        return self._run(label, lambda client: client.connectapi(path, params=params))

    def method(self, fn: Any, kwargs: dict[str, Any]) -> Any:
        return self._run(fn.__name__, lambda client: fn(client, **kwargs))


def describe(body: Any) -> str:
    if body is None:
        return "empty (204?)"
    if isinstance(body, list):
        return f"list[{len(body)}]"
    if isinstance(body, dict):
        lists = {k: len(v) for k, v in body.items() if isinstance(v, list)}
        extra = " " + " ".join(f"{k}[{n}]" for k, n in lists.items()) if lists else ""
        return f"{len(body)} keys{extra}"
    return type(body).__name__


def items(body: Any, key: str) -> list[dict[str, Any]]:
    seq = body.get(key) if isinstance(body, dict) else body
    return [x for x in seq or [] if isinstance(x, dict)]


def pagination(first0: list[Any], first1: list[Any]) -> str:
    """Compare start=0 and start=1 pages of the same list (IDs only)."""
    if len(first0) < 2 or not first1:
        return "undetermined (need at least 2 items)"
    if first0[0] == first1[0]:
        return "1-based (start=0 and start=1 return the same first item)"
    if first0[1] == first1[0]:
        return "0-based (start=1 skips the first item)"
    return "unexpected (pages don't line up)"


def pick(conns: list[dict[str, Any]], wanted: str | None) -> dict[str, Any] | None:
    if not wanted:
        return conns[0] if conns else None
    w = wanted.strip().lower()
    for c in conns:
        if str(c.get("displayName", "")).lower() == w:
            return c
    hits = [c for c in conns if str(c.get("fullName", "")).lower().startswith(w)]
    return hits[0] if len(hits) == 1 else None


def run(p: Prober, registry: Registry, me: str, wanted: str | None) -> None:
    print("[1/5] connections list")
    path = CONNECTIONS.format(dn=quote(me, safe=""))
    page0 = items(p.get("start=0 limit=3", path,
                        {"start": "0", "limit": "3", "displayMutedStatus": "false"}), "userConnections")
    page1 = items(p.get("start=1 limit=50", path,
                        {"start": "1", "limit": "50", "displayMutedStatus": "false"}), "userConnections")
    print(f"   pagination: {pagination([c.get('userId') for c in page0], [c.get('userId') for c in page1])}")
    seen = {c.get("userId"): c for c in page0 + page1}
    conns = list(seen.values())
    print(f"   connections seen: {len(conns)}")
    friend = pick(conns, wanted)
    if friend is None:
        raise SystemExit("FAILED: no connection to test (none, or --connection matched 0 or 2+)")
    dn_q = quote(str(friend["displayName"]), safe="")
    friend_id = friend.get("userId")
    print(f"   testing connection #{conns.index(friend) + 1} of {len(conns)}")

    print("[2/5] connection profile and stats")
    for label, tmpl, params in FRIEND_PATHS:
        body = p.get(label, tmpl.format(dn=dn_q), params)
        if label == "socialProfile" and isinstance(body, dict):
            print("   visibility: " + ", ".join(f"{k}={body.get(k)}" for k in VISIBILITY_KEYS))

    print("[3/5] connection activities")
    apath = ACTIVITIES.format(dn=dn_q)
    a0 = p.get("start=0 limit=3", apath, {"start": "0", "limit": "3"})
    a1 = p.get("start=1 limit=3", apath, {"start": "1", "limit": "3"})
    l0, l1 = items(a0, "activityList"), items(a1, "activityList")
    print(f"   pagination: {pagination([a.get('activityId') for a in l0], [a.get('activityId') for a in l1])}")
    if isinstance(a1, dict):
        print(f"   requestorRelationship={a1.get('requestorRelationship')} "
              f"owner matches connection={all(a.get('ownerId') == friend_id for a in l1)}")
    acts = l0 or l1
    newest = str(acts[0].get("startTimeLocal", ""))[:10] if acts else ""
    try:
        # A window ending the day before the newest activity must exclude it.
        end = date.fromisoformat(newest) - timedelta(days=1)
    except ValueError:
        print("   date filter: skipped (no activities with a parsable startTimeLocal)")
    else:
        ranged = p.get("startDate/endDate", apath, {
            "start": "0", "limit": "3",
            "startDate": (end - timedelta(days=60)).isoformat(), "endDate": end.isoformat()})
        if ranged is not None:
            got = items(ranged, "activityList")
            honoured = all(str(a.get("startTimeLocal", ""))[:10] <= end.isoformat() for a in got)
            print(f"   date filter honoured: {'yes' if honoured else 'no (ignored)'} ({len(got)} returned)")

    print("[4/5] leaderboard (steps, this week)")
    monday = date.today() - timedelta(days=date.today().weekday())
    lb = p.get("FOLLOWING metricId=29", LEADERBOARD, {
        "start": "1", "limit": "10", "metricId": "29",
        "startDate": monday.isoformat(), "endDate": (monday + timedelta(days=6)).isoformat()})
    if isinstance(lb, dict):
        print(f"   metric={lb.get('wellnessLeaderBoardMetricType')} entries={len(items(lb, 'userStats'))}")

    print("[5/5] existing registry methods on a connection's activity")
    if not acts:
        print("   skipped: no visible activities for this connection")
        return
    aid = str(acts[0]["activityId"])
    for name in REGISTRY_METHODS:
        info = registry.get(name)
        if info is None or info.access is not Access.READ:
            print(f"   skip {name:<28} not a READ method in this garminconnect version")
            continue
        res = p.method(info.fn, {"activity_id": aid})
        if name == "get_activity" and isinstance(res, dict):
            summary = res.get("summaryDTO") or {}
            print(f"   owner matches connection={res.get('userProfileId') == friend_id} "
                  f"averageHR present={'averageHR' in summary} "
                  f"averagePower present={'averagePower' in summary}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--connection", help="display name or unique full-name prefix")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between requests (min 1)")
    args = ap.parse_args()
    os.environ.setdefault("PUBLIC_BASE_URL", "http://localhost")
    settings = Settings.from_env()
    setup_logging("WARNING")
    key = get_secret(settings.fernet_key_secret_name, "GARMIN_MCP_FERNET_KEY", settings.gcp_project)
    provider = UnofficialProvider(EncryptedFileTokenStore(settings.token_dir, key),
                                  impersonate_api_tls=settings.garmin_api_tls_impersonation)
    try:
        session = provider.get_client(args.user)  # token resume; never a credential login
    except GarminError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    me = getattr(session._garmin, "display_name", None)
    if not me:
        print("FAILED: own display name not loaded", file=sys.stderr)
        return 1
    p = Prober(session, max(1.0, args.delay))
    try:
        run(p, Registry(), me, args.connection)
    except Stop as e:
        print(f"STOPPED after {p.requests} requests ({e}). Not retrying; wait before running again.",
              file=sys.stderr)
        return 3
    print(f"{p.requests} requests. " + ("OK" if not p.failures else f"{p.failures} probe(s) failed"))
    return 0 if not p.failures else 2


if __name__ == "__main__":
    sys.exit(main())
