# Plan: read your Garmin connections' data from the MCP server

Status: Phase 0 done 2026-10-21 (results below). Phase 1 built 2026-10-21 on branch `connections`; not yet deployed. Endpoints come from a capture of
connect.garmin.com browsing (connections page, one connection's profile, three of
their activities) with the out-of-repo `garmin-capture` extension. The capture
files hold connections' personal data and are never copied into this repo.

## Goal

Let Claude answer "how is Sam's training going?" or "compare my long run with
Sam's" from claude.ai, using data Garmin already shows Andrew as a connection.
Read-only, and limited to what Garmin's own privacy settings expose to him.

## Non-goals

- Social writes: follow/unfollow, accept requests, likes, comments, kudos.
- Anyone who isn't a current connection (public profiles, followers-only,
  pending requests, search for users).
- Rabbit. Its agent shares `GarminService.execute`, so these methods would show
  up there too; v1 keeps them off in `rabbit.service` (see Gating).
- New MCP tools. The surface stays `search`, `execute`, `recent_activities`.
- Wellness data (sleep, HRV, body battery) for connections. The web app doesn't
  request it for a connection's profile, and the one daily-summary call returned
  an empty list.

## What the capture showed

The web app calls `connect.garmin.com/gc-api/<service>/...`. The library calls
the same service paths on `connectapi.garmin.com` with the OAuth bearer token,
so the expectation is the same paths work through `client.connectapi(path,
params=...)`. This is not yet verified (Phase 0).

Connections are addressed by **display name** in the path (a UUID for most
accounts; Garmin also allows custom display names).

| Need | Endpoint (all GET) | Notes |
|---|---|---|
| List my connections | `/connection-service/connection/v2/connections/pagination/{me}?start=1&limit=24&displayMutedStatus=false` | `{fullName, userConnections[], pagination}`. Each entry: `userId`, `displayName`, `fullName`, `location`, `profileVisibility`, `userConnectionStatus` (2 for all seen), image URLs. Note `start=1`. |
| A connection's profile | `/userprofile-service/socialProfile/{dn}` | Visibility flags (`activityHeartRateVisibility`, `activityPowerVisibility`, `showVO2Max`, ...), favorite activity types, running/cycling/swimming training speeds, `cyclingMaxAvgPower`. |
| Their activities | `/activitylist-service/activities/{dn}?start=1&limit=10` | `{activityList[], filter, pagination, requestorRelationship}`. Entries are the same 81-key shape as my own activity list (`activityId`, `activityType`, distance, duration, HR, swim fields, `privacy`, `ownerId`...). `requestorRelationship` was `CONNECTED`. |
| Their activity details | `/activity-service/activity/{id}` and `/details`, `/splits`, `/split_summaries`, `/typedsplits`, `/hrTimeInZones`, `/powerTimeInZones`, `/weather`, `/workouts` | **Same endpoints as my own activities.** All three captured activity pages belonged to the connection and returned 200. Existing registry methods (`get_activity`, `get_activity_splits`, ...) should work unchanged with a connection's `activityId`. |
| Their totals | `/userstats-service/statistics/{dn}`, `/statistics/monthly/{dn}?ByParentType=true`, `/statistics/previousDays/{dn}` | `userMetrics[]` per activity type: count, distance, duration, calories, elevation (monthly adds `month`). |
| Lifetime totals | `/usersummary-service/stats/connectLifetimeTotals/{dn}` | Distance, steps, calories, active days. |
| Their PRs | `/personalrecord-service/personalrecord/prs/{dn}?includeHistory=false` | `typeId`, `value`, `activityId`, `activityName`, dates. Type IDs map via `/personalrecordtype/prtypes/{dn}`. |
| Leaderboard | `/usersummary-service/stats/wellness/leaderboard/FOLLOWING?start=1&limit=1000&metricId=29&startDate=..&endDate=..` | Only `metricId=29` (`TOTAL_STEPS`) was captured. |

Privacy is enforced by Garmin, not by us. For a connection, `personal-information`
returned no birth date, email, age, height, weight or VO2 max, and their activity
list only contained activities visible to connections (`includedPrivacyList`
`[1, 3, 4]`). The capture also included my own `personal-information` (email,
birth date), which this plan does not expose.

Not used: followers and pending requests (`follower-service`), badges, devices,
course and calendar calls, likes and comments (`conversation-service`), newsfeed
status, and `userpreference-service` UI flags.

## Design

### 1. `social.py`: hand-written read methods

A new module of plain functions with the same calling convention as registry
entries, `fn(client, **kwargs)`, so `GarminSession.call` runs them unchanged
(backoff, error translation, Cloudflare detection, token write-back). Each
builds its path from a **fixed template** and calls `client.connectapi(path,
params=...)`. `connectapi` stays in `ALWAYS_BLOCKED`: these functions are reviewed
code calling it, not a passthrough for user paths.

| Method | Params | Returns |
|---|---|---|
| `get_connections` | none | Compact list: `display_name`, `full_name`, `location`. Fetches all pages (capped at 500). |
| `get_connection_profile` | `connection` | Trimmed social profile: visibility flags, favorite sports, training speeds, cycling max avg power. No image URLs or social links. |
| `get_connection_activities` | `connection`, `start=0`, `limit=20` (max 100) | `compact_activity()` entries (from `tools.py`), plus `owner` and `privacy`. Same compaction as `recent_activities`, so Claude gets comparable fields for both people. |
| `get_connection_stats` | `connection`, `period` in `all`/`monthly`/`recent` | `userMetrics`, plus lifetime totals for `all`. |
| `get_connection_personal_records` | `connection` | PRs with the type name resolved from `prtypes`. |
| `get_connections_leaderboard` | `startdate`, `enddate` | Steps leaderboard (`metricId=29` only until other IDs are known). |

Each function returns `{"connection": {...}, ...}` with the resolved display name
and full name, so answers don't mix people up. For a connection's activity
details Claude uses the existing methods with the `activityId` from
`get_connection_activities`.

`start` is 0-based in our API and converted to the web app's 1-based value if
Phase 0 confirms the endpoints are 1-based.

### 2. Connection resolution (the main guard)

`connection` is never pasted straight into a URL. Before dispatch,
`GarminService` resolves it against the caller's own **cached `get_connections`
result**:

1. An exact `displayName` match, or
2. A case-insensitive match on `fullName`, or a unique prefix of it ("sam" →
   "Sam Lee"). If two match, the error lists the candidates' full names; if none
   match, the error says so and suggests `get_connections`.

Only the matched entry's `displayName` reaches the function, which still
re-checks it (non-empty, ≤ 64 chars, no control characters) and URL-encodes it
with `quote(dn, safe="")`, so a hostile `displayName` can't inject path segments
(the library does the same in `_require_display_name`). This means:

- Nobody outside the caller's connection list can be queried, even if Garmin
  would serve their public profile.
- Claude can say "Sam" without knowing UUIDs.
- Resolution costs nothing extra once the list is cached (`TTLCache`, per user).

Implementation: `MethodInfo` gets a `resolves_connection: bool` flag set for the
social methods; `GarminService.execute` runs the resolution step for flagged
methods, then dispatches as usual. The cache key uses the resolved display name.

### 3. Registry integration

`Registry(cls, extensions=social.METHODS)` adds the social functions to the index.
Extensions go through the same `_classify` checks (they must have a `get_`
prefix, a fixed signature with no variadics or path-like params, and no write
verbs in their source), so the default-deny posture is unchanged. Startup fails
if an extension name collides with a `Garmin` method, so a future
`garminconnect` release that adds, say, `get_connections` can't silently replace
ours or be shadowed. Search picks them up from their docstrings; `_SYNONYMS`
gains `friend`/`friends`/`buddy` → `connection`/`connections`.

### 4. Gating

New setting `GARMIN_SOCIAL_ENABLED` (default `false`, documented in
`.env.example`). When off, extensions aren't registered at all: `search`
doesn't list them and `execute` treats them as unknown. The MCP server's
`/etc/garmin-mcp/env` turns it on; `rabbit.service` leaves it off. In one-process
mode the flag covers both surfaces, which is acceptable because one-process mode
is local development only.

### 5. Logging and privacy

Unchanged: `execute` logs method and argument *names* only, so display names and
full names never reach the logs. Responses go through `shape()` as today.
Nothing about connections is persisted beyond the in-memory TTL cache.

## Phases

**Phase 0: verify on the VM (no server changes).** `scripts/smoke_social.py`,
modeled on `smoke_live.py` (prints status codes, counts and key names only, never
values). To protect the VM's IP it makes about 13 GETs, one at a time with a
jittered 3 s pause, never retries, and stops at the first 401/403/429 or
Cloudflare marker. It calls `client.connectapi` for each endpoint above and checks:
- Does `connectapi.garmin.com` with the bearer token serve these paths (vs. 403
  or 404, which would mean they're web-only and need `connectwebproxy` or a
  different base)?
- Is pagination 1-based (does `start=0` return a duplicate first item)?
- Does `get_activity(<connection's activityId>)` work through the existing
  registry, and are HR and power present when their visibility flags say so?
- Does `activities/{dn}` accept `startDate`/`endDate` (would allow a
  date-range variant)?

This shares the token store with the running server, like `smoke_live.py`, so
it carries the same refresh-token race. Run it right after a server call, when
the access token is fresh. Delete `~/Desktop/projects/garmin-capture/captures/`
once Phase 0 settles the field mapping.

Results (2026-10-21, one connection, 14 requests, no blocks):
- Every endpoint in the table returns 200 via `client.connectapi` on
  `connectapi.garmin.com` with the bearer token. No web-only paths.
- Pagination is 1-based for connections and activities (`start=0` and
  `start=1` return the same first item). Our `start` stays 0-based and is sent
  as `start + 1`.
- `activities/{dn}` ignores `startDate`/`endDate`. Paging only; any date-range
  variant pages back and filters on our side, with a hard page cap.
- `get_activity` and `get_activity_splits` read the connection's activity
  unchanged (owner matches, `averageHR` present). No new code is needed for a
  connection's activity details.
- `averagePower` was absent on that activity while `activityPowerVisibility`
  is public, most likely an activity recorded without power. Inconclusive, not a
  blocker.

**Phase 1: implement.** `social.py`, registry extensions with the collision
check, connection resolution in `GarminService`, the setting, search synonyms.
Tests:
- A `FakeSocialGarmin` whose `connectapi` serves canned payloads by path
  (`FakeGarmin.connectapi` must keep raising, since it exercises blocking).
- Resolution: exact display name, full name, unique prefix, ambiguous,
  unknown. The friend endpoint must not be called on failure.
- A display name containing `/`, `?`, `..` or `%2F` gets encoded into a single
  path segment.
- The flag off means nothing is registered; a name collision fails startup;
  `execute("connectapi")` stays blocked.
- Per-user isolation: user B's cached connection list never resolves user A's
  request.

**Phase 2: deploy and try it.** Set the flag in the MCP env, `./deploy.sh`, and
Andrew tries it from claude.ai against his real account. Update README (tools
section, connections note), CLAUDE.md (registry extensions, flag) and the MCP
server instructions (one line: "for a connection's data, call
get_connections first, then the get_connection_* methods").

**Later (not committed to):** leaderboards for other metrics, likes and comments
on activities, an upstream PR to `python-garminconnect`, and Rabbit support
(that needs its own answer to "may friend X's coach see Andrew's data because
they're connected?").

## Open questions

1. Other leaderboard `metricId`s: switching the leaderboard filter on the
   connections page during another capture would reveal them.
2. Whether Garmin treats heavy reads of connections' data differently from
   reads of your own (no sign of it, but we haven't tested). Keep normal
   rate limits and caching; no bulk fetches across all connections.
