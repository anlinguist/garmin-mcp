"""Goals, athlete profile, threads and goal-scoped context."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp.appstore import AppStore
from garmin_mcp.coaching import NO_GOAL, CoachError, CoachStore, training_phase
from tests.test_agent import Scripted, collect, go, tid, tool_block  # noqa: F401


@pytest.fixture
def coach(tmp_path, fernet_key) -> CoachStore:
    return CoachStore(AppStore(tmp_path / "app", fernet_key))


NOW = datetime(2026, 10, 12, 7, 0, tzinfo=ZoneInfo("America/Denver"))


@pytest.mark.parametrize("race,weeks,phase", [
    (date(2027, 6, 27), 36, "base"),
    (date(2027, 1, 25), 15, "build"),
    (date(2026, 11, 23), 6, "peak"),
    (date(2026, 10, 26), 2, "taper"),
    (date(2026, 10, 16), 0, "race week"),
    (date(2026, 10, 1), -2, "post-race"),
])
def test_training_phase(race, weeks, phase) -> None:
    assert training_phase(race, NOW.date()) == (weeks, phase)


def test_goal_lifecycle_and_coach_threads(coach) -> None:
    before = coach.coach_thread("friend")
    assert before["goal_id"] == NO_GOAL
    g1 = coach.create_goal("friend", {"event": "Ironman 70.3 Coeur d'Alene", "date": "2027-06-27",
                                      "distance": "70.3", "target": "sub-5:30"})
    assert g1["title"] == "Ironman 70.3 Coeur d'Alene"
    c1 = coach.coach_thread("friend")
    assert c1["goal_id"] == g1["id"] and c1["id"] != before["id"]
    g2 = coach.create_goal("friend", {"title": "Spring marathon", "date": "2027-04-18"})
    goals = {g["id"]: g for g in coach.goals("friend")["goals"]}
    assert goals[g1["id"]]["archived_at"] and coach.active_goal("friend")["id"] == g2["id"]
    assert coach.threads("friend")[0]["goal_id"] == g2["id"]  # each goal has its own log
    coach.archive_goal("friend", g2["id"])
    assert coach.active_goal("friend") is None


def test_notes_are_scoped_to_the_active_goal(coach) -> None:
    coach.set_notes("friend", "pre-goal note")
    g = coach.create_goal("friend", {"title": "70.3"})
    assert coach.notes("friend") == ""
    coach.set_notes("friend", "knee sore after back-to-back runs")
    coach.create_goal("friend", {"title": "next"})
    assert coach.notes("friend") == ""
    assert {x["id"]: x for x in coach.goals("friend")["goals"]}[g["id"]]["notes"] \
        == "knee sore after back-to-back runs"


def test_validation(coach) -> None:
    with pytest.raises(CoachError):
        coach.create_goal("friend", {"date": "June 27"})
    with pytest.raises(CoachError):
        coach.create_goal("friend", {"nope": 1})
    with pytest.raises(CoachError):
        coach.update_athlete("friend", {"units": "furlongs"})
    with pytest.raises(CoachError):
        coach.update_athlete("friend", {"weekly_hours": "lots"})
    assert coach.update_athlete("friend", {"weekly_hours": 9, "injuries": "  IT band  "}) == {
        "weekly_hours": 9.0, "injuries": "IT band"}
    assert coach.get_thread("friend", "../../etc") is None


def test_discuss_seeds_topic_and_links_from_log(coach) -> None:
    log = coach.coach_thread("friend")
    coach.append_turn("friend", log["id"], {"id": "t1", "ts": 1, "kind": "proactive",
                                            "label": "workout", "user": None,
                                            "assistant": "Strong tempo.", "parts": []})
    topic = coach.create_thread("friend", seed={"thread_id": log["id"], "turn_id": "t1"})
    seeded = coach.turns("friend", topic["id"])
    assert seeded[0]["kind"] == "seed" and seeded[0]["assistant"] == "Strong tempo."
    card = coach.turns("friend", log["id"])[-1]
    assert card["kind"] == "link" and card["parts"][0] == {
        "type": "thread_link", "thread_id": topic["id"], "from_turn": "t1"}
    assert [t["kind"] for t in coach.threads("friend")] == ["coach", "topic"]
    with pytest.raises(CoachError):
        coach.create_thread("friend", seed={"thread_id": log["id"], "turn_id": "missing"})
    with pytest.raises(CoachError):
        coach.delete_thread("friend", log["id"])  # the coach log can't be deleted
    coach.delete_thread("friend", topic["id"])
    assert coach.turns("friend", topic["id"]) == []


def test_legacy_conversation_migrates(coach, tmp_path, fernet_key) -> None:
    store = AppStore(tmp_path / "app", fernet_key)
    store.user_doc("friend", "conversation").save({"turns": [
        {"id": "a", "kind": "chat", "user": "hi", "assistant": "hey", "parts": []},
        {"id": "b", "kind": "proactive", "user": None, "assistant": "brief", "parts": []},
    ]})
    coach.migrate_legacy("friend")
    threads = coach.threads("friend")
    assert [t["kind"] for t in threads] == ["coach", "topic"]
    assert [t["id"] for t in coach.turns("friend", threads[0]["id"]) if t["kind"] != "link"] == ["b"]
    assert [t["id"] for t in coach.turns("friend", threads[1]["id"])] == ["a"]
    assert store.user_doc("friend", "conversation").load() == {}


def test_context_block_scopes_to_goal(coach) -> None:
    coach.update_athlete("friend", {"experience": "2 seasons of sprint tri", "weekly_hours": 8})
    coach.create_goal("friend", {"event": "70.3 Coeur d'Alene", "date": "2027-06-27",
                                 "target": "sub-5:30", "priorities": "swim is the limiter"})
    coach.set_notes("friend", "prefers morning sessions")
    block = coach.context_block("friend", NOW, coach.coach_thread("friend"))
    for want in ("Monday 2026-10-12", "2 seasons of sprint tri", "weekly hours: 8.0",
                 "36 weeks out, roughly the base phase", "Target: sub-5:30",
                 "swim is the limiter", "prefers morning sessions", "the coach log"):
        assert want in block, want


# ------------------------------------------------------- agent memory tools --
def test_agent_saves_goal_and_profile(tmp_path, fernet_key) -> None:
    from tests.test_agent import env as _env  # noqa: F401  (fixture factory reused below)
    from garmin_mcp.agent import Agent
    from garmin_mcp.providers.fake import FakeGarminProvider
    from garmin_mcp.registry import Registry
    from garmin_mcp.resilience import RateLimiter, TTLCache
    from garmin_mcp.tokens import EncryptedFileTokenStore
    from garmin_mcp.tools import GarminService

    tokens = EncryptedFileTokenStore(tmp_path / "t", fernet_key)
    store = AppStore(tmp_path / "a", fernet_key)
    svc = GarminService(FakeGarminProvider(tokens, delay=0), Registry(), allow_writes=False,
                        cache=TTLCache(1), limiter=RateLimiter(100), max_response_bytes=10_000)
    backend = Scripted([
        ([], [tool_block("save_goal", {"event": "70.3 CdA", "date": "2027-06-27"}),
              tool_block("update_athlete_profile", {"weekly_hours": 9, "units": "imperial"}, 1)],
         "tool_use"),
        (["Saved."], [], "end_turn"),
        ([], [tool_block("save_goal", {"target": "sub-5:30"}, 2),
              tool_block("save_goal", {"date": "next June"}, 3)], "tool_use"),
        (["Updated."], [], "end_turn"),
    ])
    agent = Agent(svc, store, backend, daily_token_budget=100_000)
    go(agent, "friend", "I'm doing CdA 70.3 next June, ~9 hrs/week, I use miles")
    goal = agent.coach.active_goal("friend")
    assert goal["event"] == "70.3 CdA" and goal["date"] == "2027-06-27"
    assert agent.coach.athlete("friend") == {"weekly_hours": 9.0, "units": "imperial"}
    events, emit = collect()
    go(agent, "friend", "aiming for sub 5:30", emit)
    assert agent.coach.active_goal("friend")["target"] == "sub-5:30"  # updated, not replaced
    assert len(agent.coach.goals("friend")["goals"]) == 1
    err = next(e for e in events if e["type"] == "tool-call-error")
    assert "YYYY-MM-DD" in err["error"]
