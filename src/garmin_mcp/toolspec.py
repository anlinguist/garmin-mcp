"""Tool descriptions shared by the MCP server and Rabbit's agent.

``tests/test_toolspec.py`` checks that the MCP tools' docstrings match these,
so the two front doors to GarminService can't drift apart.
"""

from __future__ import annotations

SEARCH_DESC = (
    'Find garminconnect client methods matching a free-text query (e.g. "hrv", "sleep", '
    '"training readiness", "activity splits", "power zones"). Returns name, signature, '
    "summary and read/write access for each."
)

RECENT_DESC = (
    "Compact list of recent activities (default last 7 days). sport filter: run, bike, swim, "
    "brick/multisport, strength, walk, hike. Includes date, duration, distance, HR, pace/speed, "
    "power, swim pool/strokes, training effect and activity_id (use with execute, e.g. "
    "get_activity_splits)."
)

HISTORY_DESC = (
    "The athlete's training history from Garmin in one call: weekly hours by sport, "
    "consistency (active weeks, average and recent hours, longest break), per-sport totals "
    "and longest sessions, and races and multisport events (including past triathlons from "
    "the last few years). Use it when setting up or changing a goal, and whenever you need "
    "their background, before asking them about it."
)

# The agent's execute is read-only and never returns raw time series.
AGENT_EXECUTE_DESC = (
    'Call one read-only garminconnect method by name with keyword args (a JSON object), e.g. '
    'method="get_hrv_data", args={"cdate": "2026-09-29"}. Use search first to find names and '
    "parameters. Only allowlisted read methods run. Bulky time-series arrays are dropped and "
    "responses are size-capped."
)

NOTES_DESC = (
    "Replace your coach notes for the current goal: things you've learned this training "
    "block (how sessions felt, niggles, what works, schedule changes). Rewrite the whole note "
    "each time, concise, under 2000 characters. Race details belong in save_goal and stable "
    "facts about the athlete in update_athlete_profile."
)

PROFILE_DESC = (
    "Save durable facts about the athlete that hold across goals: experience, units (metric or "
    "imperial), typical weekly hours, usual training days, injuries/limitations, preferences "
    "(e.g. 'swim distances in yards', 'hates the treadmill'), age. Only fields the athlete told "
    "you or confirmed; pass just the fields that changed."
)

GOAL_DESC = (
    "Save or update the athlete's active goal: title, event, date (YYYY-MM-DD), distance, "
    "target, priorities. Creates the goal if none is active, otherwise updates it; pass just "
    "the fields that changed. To switch to a different race (archiving the current goal), set "
    "replace_current=true, but only after the athlete has explicitly confirmed the switch."
)

WEEK_DESC = (
    "The planned sessions for this week or next, day by day, from their plan with any day "
    "changes applied (a changed day shows the reason and what the plan had). Use it before "
    "moving or swapping sessions, or when they ask about a day beyond today and tomorrow."
)

CHANGE_DAY_DESC = (
    "Change one day of their plan: replace that day's planned sessions, e.g. a run instead "
    "of the bike when their bike is broken, or a shorter session when they're short on time. "
    "To move a session to another day, change both days (look at the week first with "
    "week_plan). Pass sessions=[] to make it a rest day, and reset=true to put the plan's "
    "day back. Use it when they ask for a change or agree to one you suggested; say in one "
    "short line what you changed. Only that day changes; the plan itself stays as it is."
)

PLAN_DESC = (
    "Save your summary of the athlete's training plan on the active goal, after they upload or "
    "describe it. This summary is what you'll see in every later conversation, so make it "
    "usable on its own: the typical week (which day has which session, and which are hard), "
    "then each week or phase with dates, weekly hours, key sessions and long-session targets, "
    "recovery weeks and races. Write it as Markdown the athlete can skim: short ### headings "
    "(Overview, Typical week, Phases, Key targets, Rules) with one bullet per day, phase or "
    "target, not one long paragraph. Under 12000 characters. Pass attachment_ids of the plan files "
    "(shown as [Attached this turn: ...]) so the originals stay linked; you can reopen them later "
    "with read_file. Rewrites the whole summary; include earlier content you're keeping. Also pass "
    "start_date (the first day of week 1, YYYY-MM-DD) and weeks (the plan's length) whenever you "
    "know them; the app uses them to number weeks and show this week's sessions. To set only "
    "those, omit summary and the current summary is kept."
)

READ_FILE_DESC = (
    "Reopen one of the athlete's uploaded files (their training plan PDF, a screenshot, a text "
    "file) to read it again. Uploads are only visible in the message they were sent with; after "
    "that you see placeholders like [pdf: plan.pdf, id at...] and plan files are listed with ids "
    "in the context. Use this whenever you need details your notes or plan summary don't have, "
    "instead of asking the athlete to repeat themselves."
)

CHART_UNITS = ["none", "count", "seconds", "minutes", "hours", "miles", "km", "yards", "meters",
               "bpm", "watts", "percent"]

CHART_DESC = (
    "Show the athlete a chart in the chat (it renders as an interactive graph with a data table). "
    "Use it whenever they ask to graph, chart, plot or visualize something, or when a trend is "
    "clearer as a picture than as text. Forms: bar (compare categories, e.g. per-day or per-week "
    "totals), stackedBar (composition, e.g. weekly time split by sport), line/area (change over "
    "time, e.g. HRV, resting HR, weekly volume), donut (share of a whole), stat (one to four "
    "headline numbers). One unit per chart: never mix measures with different units (make two "
    "charts instead). Only plot numbers you actually retrieved with tools; convert to the "
    "athlete's preferred units first. Keep labels short (e.g. 'Oct 6', 'Wk 3'). After calling "
    "it, add a sentence or two on what the chart shows, but don't repeat every number."
)

AGENT_TOOLS: list[dict] = [
    {
        "name": "recent_activities",
        "description": RECENT_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "minimum": 1, "maximum": 90,
                         "description": "How many days back, including today (default 7)."},
                "sport": {"type": "string",
                          "description": "Optional filter: run, bike, swim, brick, strength, walk, hike."},
            },
        },
    },
    {
        "name": "training_history",
        "description": HISTORY_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer", "minimum": 4, "maximum": 52,
                          "description": "How many weeks back to summarize (default 16)."},
                "past_triathlon_years": {"type": "integer", "minimum": 0, "maximum": 8,
                                         "description": "Also list multisport races from this many years before that (default 5)."},
            },
        },
    },
    {
        "name": "search",
        "description": SEARCH_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute",
        "description": AGENT_EXECUTE_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "args": {"type": "object", "description": "Keyword arguments for the method."},
            },
            "required": ["method"],
        },
    },
    {
        "name": "update_athlete_profile",
        "description": PROFILE_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "experience": {"type": "string"},
                "units": {"type": "string", "enum": ["metric", "imperial"]},
                "weekly_hours": {"type": "number"},
                "age": {"type": "integer"},
                "training_days": {"type": "string"},
                "injuries": {"type": "string"},
                "preferences": {"type": "string"},
            },
        },
    },
    {
        "name": "save_goal",
        "description": GOAL_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "event": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "distance": {"type": "string"},
                "target": {"type": "string"},
                "priorities": {"type": "string"},
                "replace_current": {"type": "boolean",
                                    "description": "Start a new goal and archive the current one."},
            },
        },
    },
    {
        "name": "save_plan",
        "description": PLAN_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "maxLength": 12000},
                "attachment_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "start_date": {"type": "string", "description": "First day of week 1, YYYY-MM-DD"},
                "weeks": {"type": "integer", "minimum": 1, "maximum": 104},
            },
        },
    },
    {
        "name": "week_plan",
        "description": WEEK_DESC,
        "input_schema": {
            "type": "object",
            "properties": {"week": {"type": "string", "enum": ["this", "next"]}},
        },
    },
    {
        "name": "change_day",
        "description": CHANGE_DAY_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "sessions": {"type": "array", "maxItems": 4, "items": {
                    "type": "object",
                    "properties": {
                        "sport": {"type": "string", "enum": ["swim", "bike", "run", "brick", "strength", "rest", "other"]},
                        "title": {"type": "string", "description": "Short, e.g. \"Run 40' easy\""},
                        "minutes": {"type": ["integer", "null"]},
                        "detail": {"type": "string", "description": "Zones, intervals, distance"},
                    },
                    "required": ["sport", "title"],
                }},
                "reason": {"type": "string", "description": "Short, shown on their week card, e.g. \"Flat tire\""},
                "reset": {"type": "boolean", "description": "Put the plan's sessions back for this day."},
            },
            "required": ["date"],
        },
    },
    {
        "name": "render_chart",
        "description": CHART_DESC,
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bar", "stackedBar", "line", "area", "donut", "stat"]},
                "title": {"type": "string", "maxLength": 120},
                "x": {"type": "array", "items": {"type": "string"},
                      "description": "Category or time labels (bar/stackedBar/line/area). Omit for donut/stat."},
                "series": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"},
                                   "data": {"type": "array", "items": {"type": "number"}}},
                    "required": ["data"]}},
                "unit": {"type": "string", "enum": CHART_UNITS},
            },
            "required": ["type", "series"],
        },
    },
    {
        "name": "read_file",
        "description": READ_FILE_DESC,
        "input_schema": {
            "type": "object",
            "properties": {"attachment_id": {"type": "string", "description": "e.g. at1a2b3c..."}},
            "required": ["attachment_id"],
        },
    },
    {
        "name": "update_notes",
        "description": NOTES_DESC,
        "input_schema": {
            "type": "object",
            "properties": {"notes": {"type": "string", "maxLength": 2000}},
            "required": ["notes"],
        },
    },
]
