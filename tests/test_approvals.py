"""Human-in-the-loop approval for changes to an established goal or plan."""

from __future__ import annotations

import asyncio
import json

import pytest

from garmin_mcp import webchat
from garmin_mcp.webchat import ChatRunner
from tests.test_agent import env, tid, tool_block  # noqa: F401  (env is a fixture)


def _setup(env, steps):
    agent, backend = env.agent(steps)
    agent.coach.create_goal("friend", {"event": "70.3 CdA", "date": "2027-06-27"})
    agent.coach.set_plan("friend", "Wk 1-4 base")
    return agent, backend, ChatRunner(agent)


async def _until(turn, kind):
    for _ in range(200):
        hit = [e for e in turn.events if e["type"] == kind]
        if hit:
            return hit[-1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind}")


def test_approved_goal_change_is_saved(env) -> None:
    agent, backend, runner = _setup(env, [
        ([], [tool_block("save_goal", {"date": "2027-06-12", "target": "sub-5:30"})], "tool_use"),
        (["Updated."], [], "end_turn")])

    async def go() -> None:
        turn = runner.start("friend", "race moved to Jun 12", thread_id=tid(agent, "friend"))
        req = await _until(turn, "approval-request")
        assert req["summary"]["title"] == "Update your goal?"
        assert {"field": "Date", "from": "2027-06-27", "to": "2027-06-12"} in req["summary"]["changes"]
        assert agent.coach.active_goal("friend")["date"] == "2027-06-27"  # not yet
        assert runner.resolve("andrew", turn.id, req["approvalCallId"], True, None) is False  # wrong user
        assert runner.resolve("friend", turn.id, req["approvalCallId"], True, None) is True
        await runner.wait_idle()
        assert agent.coach.active_goal("friend")["date"] == "2027-06-12"
        part = agent.coach.turns("friend", tid(agent, "friend"))[-1]["parts"][0]
        assert part["approval"]["approved"] is True
    asyncio.run(go())


def test_declined_change_is_not_saved_and_model_hears_why(env) -> None:
    agent, backend, runner = _setup(env, [
        ([], [tool_block("save_goal", {"replace_current": True, "event": "Boise 70.3"})], "tool_use"),
        (["Okay, keeping CdA."], [], "end_turn")])

    async def go() -> None:
        turn = runner.start("friend", "thinking about Boise", thread_id=tid(agent, "friend"))
        req = await _until(turn, "approval-request")
        assert req["summary"]["title"] == "Switch to a new goal?"
        runner.resolve("friend", turn.id, req["approvalCallId"], False, "just thinking out loud")
        await runner.wait_idle()
        assert agent.coach.active_goal("friend")["event"] == "70.3 CdA"
        assert len(agent.coach.goals("friend")["goals"]) == 1
        result = backend.calls[1][-1]["content"][0]
        assert result["is_error"] and "just thinking out loud" in result["content"]
    asyncio.run(go())


def test_plan_rewrite_needs_approval_first_plan_does_not(env) -> None:
    agent, backend = env.agent([
        ([], [tool_block("save_goal", {"event": "70.3"}), tool_block("save_plan", {"summary": "v1"}, 1)],
         "tool_use"),
        (["ok"], [], "end_turn")])
    from tests.test_agent import go
    events = []
    go(agent, "andrew", "here's my plan", events.append)  # onboarding: no approvals needed
    assert not any(e["type"] == "approval-request" for e in events)
    assert agent.coach.active_goal("andrew")["plan"] == "v1"
    assert agent.approval_needed("andrew", "save_plan", {"summary": "v2"})["title"] == \
        "Replace your plan summary?"
    assert agent.approval_needed("andrew", "save_goal", {"event": "70.3"}) is None  # no change
    assert agent.approval_needed("andrew", "update_notes", {"notes": "x"}) is None


def test_background_turns_cannot_change_goal(env) -> None:
    agent, backend, runner = _setup(env, [
        ([], [tool_block("save_goal", {"date": "2027-07-01"})], "tool_use"),
        (["Brief text."], [], "end_turn")])

    async def go() -> None:
        turn = runner.start("friend", "brief", thread_id=tid(agent, "friend"), kind="proactive")
        await runner.wait_idle()
        assert not any(e["type"] == "approval-request" for e in turn.events)
        assert agent.coach.active_goal("friend")["date"] == "2027-06-27"
        assert "can't happen in a background message" in backend.calls[1][-1]["content"][0]["content"]
    asyncio.run(go())


def test_unanswered_approval_times_out_as_declined(env, monkeypatch) -> None:
    monkeypatch.setattr(webchat, "APPROVAL_TIMEOUT", 0.05)
    agent, backend, runner = _setup(env, [
        ([], [tool_block("save_plan", {"summary": "v2"})], "tool_use"),
        (["ok"], [], "end_turn")])

    async def go() -> None:
        turn = runner.start("friend", "new plan", thread_id=tid(agent, "friend"))
        await runner.wait_idle()
        res = next(e for e in turn.events if e["type"] == "approval-result")
        assert res["approved"] is False and "10 minutes" in res["reason"]
        assert agent.coach.active_goal("friend")["plan"] == "Wk 1-4 base"
    asyncio.run(go())
