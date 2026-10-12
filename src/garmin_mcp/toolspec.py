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

PLAN_DESC = (
    "Save your summary of the athlete's training plan on the active goal, after they upload or "
    "describe it. This summary is what you'll see in every later conversation, so make it "
    "usable on its own: the typical week (which day has which session, and which are hard), "
    "then each week or phase with dates, weekly hours, key sessions and long-session targets, "
    "recovery weeks and races. Under 12000 characters. Pass attachment_ids of the plan files "
    "(shown as [Attached this turn: ...]) so the originals stay linked; you can reopen them later "
    "with read_file. Rewrites the whole summary; include earlier content you're keeping."
)

READ_FILE_DESC = (
    "Reopen one of the athlete's uploaded files (their training plan PDF, a screenshot, a text "
    "file) to read it again. Uploads are only visible in the message they were sent with; after "
    "that you see placeholders like [pdf: plan.pdf, id at...] and plan files are listed with ids "
    "in the context. Use this whenever you need details your notes or plan summary don't have, "
    "instead of asking the athlete to repeat themselves."
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
            },
            "required": ["summary"],
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
