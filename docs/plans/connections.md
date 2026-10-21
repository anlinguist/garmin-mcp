# Connections: reading your Garmin connections' data

Status: shipped 2026-10-21 on the MCP server and Rabbit (`GARMIN_SOCIAL_ENABLED=true`
in `/etc/garmin-mcp/env` and `/etc/rabbit/env`).

Claude (in claude.ai) and Rabbit's coach can answer "how is Sam's training going?"
or "compare my long run with Sam's" from data Garmin already shows the user as a
connection. Read-only, limited to the caller's own connections and to what each
connection shares with them.

Endpoints were found by capturing connect.garmin.com traffic with an out-of-repo
browser extension (`garmin-capture`) and confirmed from the VM with
`scripts/smoke_social.py`. Capture files hold connections' personal data and are
never copied into this repo.

## Non-goals

- Social writes: follow/unfollow, accepting requests, likes, comments.
- Anyone who isn't a current connection (public profiles, followers, pending
  requests, user search).
- New MCP tools. The surface stays `search`, `execute`, `recent_activities`.
- Connections' wellness data (sleep, HRV, body battery). The web app doesn't
  request it for a connection's profile.

## Endpoints

All GET on `connectapi.garmin.com` through `client.connectapi` with the library's
bearer token (the web app uses the same paths under `connect.garmin.com/gc-api/`
with its session cookie). Connections are addressed by **display name** (`{dn}`): a
UUID for most accounts, but Garmin also allows custom ones.

| Need | Endpoint | Notes |
|---|---|---|
| My connections | `/connection-service/connection/v2/connections/pagination/{me}?start=&limit=&displayMutedStatus=false` | `userConnections[]`: `userId`, `displayName`, `fullName`, `location`, image URLs. |
| Profile | `/userprofile-service/socialProfile/{dn}` | Visibility flags, favorite sports, training speeds, `cyclingMaxAvgPower`. |
| Activities | `/activitylist-service/activities/{dn}?start=&limit=` | Same entry shape as your own activity list, plus `privacy` and `ownerId`. `requestorRelationship` is `CONNECTED`. |
| Activity details | `/activity-service/activity/{id}` and its subpaths | The library's own endpoints. `get_activity`, `get_activity_splits`, etc. work unchanged with a connection's `activityId`. |
| Totals | `/userstats-service/statistics/{dn}`, `/statistics/monthly/{dn}?ByParentType=true`, `/statistics/previousDays/{dn}` | `userMetrics[]` per activity type. |
| Lifetime totals | `/usersummary-service/stats/connectLifetimeTotals/{dn}` | Distance, steps, calories, active days. |
| PRs | `/personalrecord-service/personalrecord/prs/{dn}?includeHistory=false` | Type IDs map via `/personalrecordtype/prtypes/{dn}` (`pr.label.5k.run`, ...). |
| Leaderboard | `/usersummary-service/stats/wellness/leaderboard/FOLLOWING?metricId=29&startDate=&endDate=` | `metricId=29` is `TOTAL_STEPS`, the only one captured. Entries carry `userProfilePk` (= connection `userId`), not names. |

Quirks (verified from the VM, 2026-10-21):

- Pagination is **1-based** for connections and activities (`start=0` and
  `start=1` return the same first item). The leaderboard is called with
  `start=1` as the web app does.
- `activities/{dn}` **ignores** `startDate`/`endDate`.
- Activity lists give `poolLength` in **centimeters**, with
  `unitOfPoolLength.factor` in cm per unit (2286 with factor 91.44 for a 25 yd
  pool). This applies to your own activity list too; `compact_activity`
  converts it. Activity details give it in the pool's unit.
- Privacy is enforced by Garmin: a connection's `personal-information` has no
  birth date, email, age, height, weight or VO2 max, and their activity list only
  contains activities shared with connections.

## Design

`src/garmin_mcp/social.py` holds six plain functions with the registry's calling
convention, `fn(client, **kwargs)`, so `GarminSession.call` runs them unchanged
(error translation, Cloudflare detection, token write-back). Each builds its path
from a fixed template and calls `client.connectapi`, which stays in
`ALWAYS_BLOCKED` for direct `execute` calls.

| Method | Params | Returns |
|---|---|---|
| `get_connections` | none | `display_name`, `full_name`, `location`, `user_id`; pages of 100, at most 500. |
| `get_connection_profile` | `connection` | Trimmed social profile (no image URLs or social links). |
| `get_connection_activities` | `connection`, `start=0`, `limit=20` (max 100) | `compact_activity()` entries plus `privacy`, the same shape as `recent_activities`. |
| `get_connection_stats` | `connection`, `period` = `all`/`monthly`/`recent` | `userMetrics` in Garmin's units, plus lifetime totals for `all`. |
| `get_connection_personal_records` | `connection` | PRs with type names from `prtypes`. |
| `get_connections_leaderboard` | `startdate`, `enddate` (≤ 31 days) | Steps ranking with names from your connections, `you` for yourself, private totals hidden. |

Our `start` is 0-based and sent to Garmin as `start + 1`.

**Connection resolution (the main guard).** A `connection` argument never goes
into a URL as given. `GarminService._resolve_connection` matches it against the
caller's own cached `get_connections` result, case-insensitively and with
whitespace collapsed, in order: exact display name, exact full name, full-name
prefix, then word prefix ("lee" → "Sam Lee"). The first stage with exactly one
hit wins; several hits is an error listing their names; no hit is an error
pointing at `get_connections`. Only the matched entry's display name reaches the
function, which re-checks it against `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` (so no
`/`, `?`, `%`, spaces or dot-segments) and URL-encodes it. The `execute` result
includes `connection: {display_name, full_name}` so answers don't mix people up.
Nobody outside the caller's list can be queried, and the cached list is per user.

**Registry.** `Registry(extensions=social.METHODS)` classifies extensions like
library methods; each must come out READ, and a name collision with a `Garmin`
method fails startup (so a future `garminconnect` method can't silently replace
ours or be shadowed). Extensions whose params include `connection` get
`MethodInfo.resolves_connection`. Search finds them through their docstrings and
the `friend`/`friends`/`buddy` synonyms.

**Gating.** `GARMIN_SOCIAL_ENABLED` (default `false`). When off, the methods aren't
registered: `search` doesn't list them and `execute` treats them as unknown. When
on, the MCP instructions gain one line (`server.SOCIAL_INSTRUCTIONS`), and Rabbit's
coach prompt gains `agent.SOCIAL_PROMPT`: use friends' data only when the athlete
asks, never volunteer it in briefs or workout notes, ask which friend on a miss or
ambiguity, say "not shared" rather than guess, keep comparisons about training,
and don't save a friend's data to the athlete's notes, goal or profile. Each Rabbit
user's coach reads only that user's own connections.

**Logging and privacy.** `execute` logs method and argument names only, so names
and display names never reach the logs. Nothing about connections is persisted
beyond the in-memory TTL cache.

## Operating it

- `scripts/smoke_social.py` re-checks every endpoint from the VM (about 14 GETs,
  3 s jittered pauses, no retries, stops at the first 401/403/429 or Cloudflare
  marker; prints only statuses, counts and yes/no answers). Run it after a
  `garminconnect` upgrade or if the friend methods start failing, shortly after a
  server call so neither process needs to refresh the shared Garmin token.
- Tests: `tests/test_social.py` (resolution, path safety, gating, per-user
  isolation, each method's shaping) and the prompt-gating test in
  `tests/test_agent.py`.

## Open questions

1. Other leaderboard `metricId`s: switching the leaderboard filter on the
   connections page during another capture would reveal them.
2. Whether Garmin treats heavy reads of connections' data differently from reads
   of your own (no sign of it so far). Keep normal rate limits and caching; no bulk
   fetches across all connections.
3. Whether the coach may save what the athlete *tells* it about a friend (in the first dev conversation it saved
   which city a friend trains in, after the athlete mentioned it). The rule was aimed at
   copying a friend's Garmin data; it may be worth narrowing to that.

**Later (not committed to):** other leaderboard metrics, likes and comments on
activities, and an upstream PR to `python-garminconnect`.
