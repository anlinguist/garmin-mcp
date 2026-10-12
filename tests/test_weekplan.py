"""Plan weeks, save_plan dates, and the coach log's "This week" (planned vs. done)."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx2
import pytest
from starlette.applications import Starlette

from garmin_mcp.coaching import CoachError, plan_week
from garmin_mcp.config import Settings
from garmin_mcp.webapp import build_app_routes
from garmin_mcp.webauth import WebAuth
from garmin_mcp.webchat import ChatRunner
from garmin_mcp.weekplan import WeekPlanner, _clean_week, merge, sport_of
from tests.test_agent import env, go, tool_block  # noqa: F401  (fixture)

BASE = "http://localhost:8001"


def test_plan_week_follows_the_plan() -> None:
    # Plan starts on a Sunday: weeks run Sun-Sat.
    g = {"plan_start": "2026-09-27", "plan_weeks": 39}
    pw = plan_week(g, date(2026, 10, 14))
    assert pw == {"start": "2026-10-11", "end": "2026-10-17", "index": 3, "weeks": 39}
    assert plan_week(g, date(2026, 10, 14), offset=-1)["index"] == 2
    # No start, but length + race date: week 1 counts back from race week (Mon-Sun).
    g = {"plan_weeks": 20, "date": "2027-02-27"}  # a Saturday
    pw = plan_week(g, date(2026, 10, 14))
    assert pw["start"] == "2026-10-12" and pw["index"] == 1
    assert plan_week(g, date(2027, 2, 27))["index"] == 20
    # Nothing known: calendar Mon-Sun, unnumbered.
    assert plan_week(None, date(2026, 10, 14)) == {
        "start": "2026-10-12", "end": "2026-10-18", "index": None, "weeks": None}


def test_set_plan_dates_and_approval(env) -> None:
    agent, _ = env.agent([])
    agent.coach.create_goal("friend", {"title": "70.3", "date": "2027-06-26"})
    with pytest.raises(CoachError):
        agent.coach.set_plan("friend", None, start_date="2026-09-28")  # no summary yet
    agent.coach.set_plan("friend", "Wk 1-4 base")
    # Adding dates for the first time isn't a plan change; no approval, summary kept.
    assert agent.approval_needed("friend", "save_plan", {"start_date": "2026-09-28", "weeks": 39}) is None
    g = agent.coach.set_plan("friend", None, start_date="2026-09-28", weeks=39)
    assert (g["plan"], g["plan_start"], g["plan_weeks"]) == ("Wk 1-4 base", "2026-09-28", 39)
    ask = agent.approval_needed("friend", "save_plan", {"weeks": 40})
    assert ask["title"] == "Change your plan dates?" and ask["changes"][0]["to"] == "40"
    for bad in ({"start_date": "next monday"}, {"weeks": 0}, {"weeks": True}):
        with pytest.raises(CoachError):
            agent.coach.set_plan("friend", None, **bad)
    ctx = agent.coach.context_block("friend", agent._now("friend"), None)
    assert " of 39 of the plan (" in ctx


def test_sport_mapping() -> None:
    assert [sport_of(k) for k in ("lap_swimming", "open_water_swimming", "road_biking",
                                  "indoor_cycling", "virtual_ride", "trail_running",
                                  "treadmill_running", "strength_training", "multi_sport",
                                  "hiking", None)] == [
        "swim", "swim", "bike", "bike", "bike", "run", "run", "strength", "brick", "other", "other"]


def test_clean_week_drops_junk() -> None:
    start = date(2026, 10, 12)
    week = _clean_week({"focus": "Build", "week_number": 3, "days": [
        {"date": "2026-10-12", "sessions": [{"sport": "swim", "title": "Swim 3x400", "minutes": 45}]},
        {"date": "2026-10-13", "sessions": [{"sport": "skydive", "title": "x", "minutes": -5}]},
        {"date": "2026-11-01", "sessions": [{"sport": "run", "title": "outside the week"}]},
        "nonsense",
    ]}, start)
    assert list(week["days"]) == [(start + timedelta(days=i)).isoformat() for i in range(7)]
    assert week["days"]["2026-10-12"][0]["minutes"] == 45
    assert week["days"]["2026-10-13"] == [{"sport": "other", "title": "x", "minutes": None, "detail": ""}]
    assert week["week_number"] == 3 and sum(map(len, week["days"].values())) == 2


def test_merge_marks_done_missed_and_extra() -> None:
    start, today = date(2026, 10, 12), date(2026, 10, 14)
    planned = {
        "2026-10-12": [{"sport": "swim", "title": "Swim"}, {"sport": "run", "title": "Easy run"}],
        "2026-10-13": [{"sport": "rest", "title": "Rest"}],
        "2026-10-14": [{"sport": "brick", "title": "Brick", "minutes": 90}],
        "2026-10-15": [{"sport": "bike", "title": "Intervals", "minutes": 60}],
    }
    done = [
        {"activity_id": 1, "date": "2026-10-12 06:00:00", "sport": "lap_swimming", "duration": "45:00"},
        {"activity_id": 2, "date": "2026-10-13 07:00:00", "sport": "yoga", "duration": "30:00"},
        {"activity_id": 3, "date": "2026-10-14 06:00:00", "sport": "multi_sport", "duration": "1:30:00",
         "multisport": {"is_parent": True}},
        {"activity_id": 4, "date": "2026-10-14 06:00:00", "sport": "road_biking", "duration": "1:00:00",
         "multisport": {"is_parent": False, "parent_id": 3}},
    ]
    days = merge(start, today, planned, done)
    mon, tue, wed, thu = days[:4]
    assert [s["status"] for s in mon["sessions"]] == ["done", "missed"]
    assert mon["sessions"][0]["activity"]["minutes"] == 45
    assert tue["sessions"][0]["status"] == "rest" and tue["extra"][0]["sport"] == "strength"
    assert wed["today"] and wed["sessions"][0]["status"] == "done" and wed["extra"] == []  # leg folded in
    assert thu["sessions"][0]["status"] == "planned"
    assert [d["weekday"] for d in days] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _week_args(start: date) -> dict:
    return {"focus": "Base: build aerobic volume", "week_number": 3, "days": [
        {"date": (start + timedelta(days=i)).isoformat(),
         "sessions": [{"sport": "run", "title": "Easy run", "minutes": 40}] if i % 2 == 0 else []}
        for i in range(7)]}


def test_week_endpoint_caches_and_counts_budget(env, tmp_path) -> None:
    agent, backend = env.agent([])
    race = (agent._now("friend").date() + timedelta(days=300)).isoformat()
    agent.coach.create_goal("friend", {"title": "70.3", "date": race})
    pdf = agent.attachments.save("friend", "plan.pdf", b"%PDF-1.4\n%%EOF")
    agent.coach.set_plan("friend", "Wk 1-4 base", [{k: pdf[k] for k in ("id", "name", "kind", "mime", "size")}])
    start = date.fromisoformat(plan_week(agent.coach.active_goal("friend"), agent._now("friend").date())["start"])
    backend.steps = [([], [tool_block("save_week", _week_args(start))], "tool_use")]
    settings = Settings(public_base_url=BASE, data_dir=tmp_path, app_enabled=True, mcp_enabled=False)
    auth = WebAuth(settings, env.store, "", dev_user="friend")
    app = Starlette(routes=build_app_routes(settings, auth, env.tokens, None, None, ChatRunner(agent)))

    async def go_() -> None:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE) as c:
            a, b = await asyncio.gather(c.get("/app/api/plan/week"), c.get("/app/api/plan/week"))
            assert a.json() == b.json()
            w = a.json()
            assert w["plan_status"] == "ready" and w["focus"].startswith("Base")
            assert w["index"] == 3 and len(w["days"]) == 7 and w["phase"] == "base"
            assert w["totals"]["planned_sessions"] == 4 and w["totals"]["planned_minutes"] == 160
            assert w["garmin_error"] is None
    asyncio.run(go_())
    assert len(backend.calls) == 1  # second request waited for and hit the cache
    sent = backend.calls[0][0]["content"]
    assert sent[0]["type"] == "document" and "Wk 1-4 base" in sent[-1]["text"]
    assert agent._used_today("friend", agent._now("friend").date().isoformat()) == 120

    # A new plan version re-plans; a planner that doesn't call the tool fails soft.
    agent.coach.set_plan("friend", "Wk 1-4 base, swim Mondays")
    backend.steps = [(["no tool"], [], "end_turn")]
    w = asyncio.run(WeekPlanner(agent).week("friend"))
    assert w["plan_status"] == "error" and all(not d["sessions"] for d in w["days"])


def test_week_without_plan_or_garmin(env) -> None:
    agent, backend = env.agent([])
    w = asyncio.run(WeekPlanner(agent).week("andrew"))  # no goal, Garmin not connected
    assert w["plan_status"] == "none" and w["index"] is None and w["garmin_error"]
    assert backend.calls == []


def test_week_over_budget_skips_planner(env) -> None:
    agent, backend = env.agent([], budget=10)
    agent.coach.create_goal("friend", {"title": "70.3"})
    agent.coach.set_plan("friend", "plan")
    agent._add_usage("friend", agent._now("friend").date().isoformat(), 50)
    w = asyncio.run(WeekPlanner(agent).week("friend"))
    assert w["plan_status"] == "over_budget" and backend.calls == []


def test_save_plan_tool_accepts_dates(env) -> None:
    agent, backend = env.agent([
        ([], [tool_block("save_goal", {"title": "70.3"}),
              tool_block("save_plan", {"summary": "v1", "start_date": "2026-09-28", "weeks": 39}, 1)],
         "tool_use"),
        (["Saved."], [], "end_turn")])
    go(agent, "friend", "here's my plan")
    g = agent.coach.active_goal("friend")
    assert (g["plan_start"], g["plan_weeks"]) == ("2026-09-28", 39)
