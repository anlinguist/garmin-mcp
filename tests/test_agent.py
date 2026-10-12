"""Rabbit's coach agent and chat runner, with a scripted model and fake Garmin data.

The model is scripted (no API calls); Garmin reads go through the real
registry and GarminService over FakeGarminProvider.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from garmin_mcp.agent import MAX_ITERATIONS, Agent
from garmin_mcp.appstore import AppStore
from garmin_mcp.providers.fake import FakeGarminProvider
from garmin_mcp.registry import Registry
from garmin_mcp.resilience import RateLimiter, TTLCache
from garmin_mcp.tokens import EncryptedFileTokenStore
from garmin_mcp.toolspec import AGENT_TOOLS
from garmin_mcp.tools import GarminService
from garmin_mcp.webchat import ChatRunner, TurnBusy


def text_block(t: str) -> Any:
    return SimpleNamespace(type="text", text=t)


def tool_block(name: str, inp: dict[str, Any], i: int = 0) -> Any:
    return SimpleNamespace(type="tool_use", id=f"tu_{name}_{i}", name=name, input=inp)


class Scripted:
    """Each script step: (texts, tool_uses, stop_reason)."""

    model = "scripted"

    def __init__(self, steps: list[tuple[list[str], list[Any], str]]) -> None:
        self.steps = list(steps)
        self.calls: list[list[dict[str, Any]]] = []
        self.gate: asyncio.Event | None = None

    async def turn(self, *, system, tools, messages, on_text):
        self.calls.append(json.loads(json.dumps(messages, default=lambda o: o.__dict__)))
        if self.gate is not None:
            await self.gate.wait()
        texts, tools_used, stop = self.steps.pop(0)
        for t in texts:
            await on_text(t)
        return SimpleNamespace(content=[text_block(t) for t in texts] + tools_used,
                               stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                     cache_read_input_tokens=0))


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry()  # the real garminconnect method surface


@pytest.fixture
def env(tmp_path, fernet_key, registry):
    tokens = EncryptedFileTokenStore(tmp_path / "tokens", fernet_key)
    tokens.save("friend", '{"fake": true}')  # friend has connected Garmin; andrew hasn't
    store = AppStore(tmp_path / "app", fernet_key)
    service = GarminService(FakeGarminProvider(tokens, delay=0), registry, allow_writes=False,
                            cache=TTLCache(300), limiter=RateLimiter(100), max_response_bytes=60_000)

    def agent(steps, **kw) -> tuple[Agent, Scripted]:
        backend = Scripted(steps)
        return Agent(service, store, backend, daily_token_budget=kw.get("budget", 10_000)), backend
    return SimpleNamespace(tokens=tokens, store=store, service=service, agent=agent)


def run(coro):
    return asyncio.run(coro)


def collect() -> tuple[list[dict[str, Any]], Any]:
    events: list[dict[str, Any]] = []
    return events, events.append


# ------------------------------------------------------------------ turns --
def test_text_only_turn(env) -> None:
    agent, backend = env.agent([(["Nice ", "work!"], [], "end_turn")])
    events, emit = collect()
    rec = run(agent.run("friend", "How am I doing?", emit))
    assert [e["type"] for e in events] == ["text-delta", "text-delta", "usage", "result"]
    assert events[-1]["response"]["status"] == "done"
    assert rec["assistant"] == "Nice work!" and rec["user"] == "How am I doing?"
    prompt = backend.calls[0][-1]["content"]
    assert prompt.startswith("[Context: today is ") and prompt.endswith("How am I doing?")
    assert agent.history("friend")[-1]["parts"] == [{"type": "text", "text": "Nice work!"}]


def test_tool_turn_uses_fake_garmin_data(env) -> None:
    agent, backend = env.agent([
        ([], [tool_block("recent_activities", {"days": 7})], "tool_use"),
        (["You had a big week."], [], "end_turn"),
    ])
    events, emit = collect()
    run(agent.run("friend", "What did I do this week?", emit))
    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["tool-call-start", "tool-call-result"]
    result = events[1]["result"]
    assert result["count"] >= 4 and result["activities"][0]["activity_id"]
    tool_result = backend.calls[1][-1]["content"][0]
    assert tool_result["tool_use_id"] == "tu_recent_activities_0"
    assert '"activities"' in tool_result["content"] and "is_error" not in tool_result
    parts = agent.history("friend")[-1]["parts"]
    assert [p["type"] for p in parts] == ["tool_call", "text"]


def test_execute_reads_but_never_writes(env) -> None:
    agent, backend = env.agent([
        ([], [tool_block("execute", {"method": "get_sleep_data", "args": {"cdate": "2026-10-10"}}),
              tool_block("execute", {"method": "delete_activity", "args": {"activity_id": "1"}}, 1)],
         "tool_use"),
        (["ok"], [], "end_turn"),
    ])
    events, emit = collect()
    run(agent.run("friend", "sleep?", emit))
    ok = next(e for e in events if e["type"] == "tool-call-result")
    assert ok["result"]["method"] == "get_sleep_data"
    err = next(e for e in events if e["type"] == "tool-call-error")
    assert "delete_activity" in err["error"] or "not" in err["error"].lower()
    assert backend.calls[1][-1]["content"][1]["is_error"] is True


def test_unconnected_user_gets_reconnect_message(env) -> None:
    agent, _ = env.agent([
        ([], [tool_block("recent_activities", {})], "tool_use"),
        (["Please connect Garmin."], [], "end_turn"),
    ])
    events, emit = collect()
    run(agent.run("andrew", "hi", emit))
    err = next(e for e in events if e["type"] == "tool-call-error")
    assert "Reconnect Garmin in Rabbit" in err["error"]
    assert "garmin-mcp-login" not in err["error"]  # no server-operator instructions for app users


def test_notes_persist_into_later_context(env) -> None:
    agent, backend = env.agent([
        ([], [tool_block("update_notes", {"notes": "70.3 CdA June 27 2027. Swims Thu."})], "tool_use"),
        (["Noted."], [], "end_turn"),
        (["Hi again"], [], "end_turn"),
    ])
    run(agent.run("friend", "My race is CdA", lambda e: None))
    run(agent.run("friend", "what's my race?", lambda e: None))
    second = backend.calls[2]
    assert "70.3 CdA June 27 2027" in second[-1]["content"]
    # earlier turn is in context as plain text, without tool calls
    assert second[0] == {"role": "user", "content": "My race is CdA"}
    assert second[1] == {"role": "assistant", "content": "Noted."}


def test_daily_budget_stops_before_calling_model(env) -> None:
    agent, backend = env.agent([(["a"], [], "end_turn")] * 3, budget=150)
    run(agent.run("friend", "1", lambda e: None))  # 120 tokens
    run(agent.run("friend", "2", lambda e: None))  # 240 > 150 after this
    events, emit = collect()
    run(agent.run("friend", "3", emit))
    assert len(backend.calls) == 2
    assert events[-1]["response"]["status"] == "error"
    assert "limit" in events[-1]["response"]["error"]


def test_refusal_and_iteration_cap(env) -> None:
    agent, _ = env.agent([([], [], "refusal")])
    events, emit = collect()
    run(agent.run("friend", "x", emit))
    assert "can't help" in "".join(e.get("text", "") for e in events)

    loop = [([], [tool_block("search", {"query": "sleep"}, i)], "tool_use") for i in range(MAX_ITERATIONS)]
    agent, backend = env.agent(loop)
    events, emit = collect()
    run(agent.run("friend", "x", emit))
    assert len(backend.calls) == MAX_ITERATIONS
    assert events[-1]["response"]["status"] == "done"


def test_agent_refuses_writable_service(env, registry) -> None:
    svc = GarminService(FakeGarminProvider(env.tokens), registry, allow_writes=True,
                        cache=TTLCache(1), limiter=RateLimiter(1), max_response_bytes=1000)
    with pytest.raises(RuntimeError):
        Agent(svc, env.store, Scripted([]), daily_token_budget=1)


def test_no_tool_lets_the_model_pick_a_user() -> None:
    for t in AGENT_TOOLS:
        props = t["input_schema"].get("properties", {})
        assert not any("user" in k.lower() for k in props), t["name"]


def test_users_are_isolated(env) -> None:
    agent, backend = env.agent([
        (["friend reply"], [], "end_turn"),
        ([], [tool_block("recent_activities", {})], "tool_use"),
        (["andrew reply"], [], "end_turn"),
    ])
    run(agent.run("friend", "friend secret question", lambda e: None))
    events, emit = collect()
    run(agent.run("andrew", "hello", emit))
    andrew_ctx = json.dumps(backend.calls[1])
    assert "friend secret question" not in andrew_ctx and "friend reply" not in andrew_ctx
    # andrew has no Garmin tokens: friend's connection is never used for his calls
    assert any(e["type"] == "tool-call-error" for e in events)
    assert [t["user"] for t in agent.history("andrew")] == ["hello"]
    assert [t["user"] for t in agent.history("friend")] == ["friend secret question"]


def test_context_starts_with_user_even_after_proactive(env) -> None:
    def seed(d):
        d["turns"] = [{"id": "p", "kind": "proactive", "user": None, "assistant": "Nice ride!"},
                      {"id": "c", "kind": "chat", "user": "thanks", "assistant": "anytime"}]
    env.store.user_doc("friend", "conversation").update(seed)
    agent, backend = env.agent([(["ok"], [], "end_turn")])
    run(agent.run("friend", "q", lambda e: None))
    assert backend.calls[0][0] == {"role": "user", "content": "thanks"}


# ---------------------------------------------------------------- runner ----
def test_runner_detaches_and_reattaches(env) -> None:
    async def go() -> None:
        agent, backend = env.agent([(["part one ", "part two"], [], "end_turn")])
        backend.gate = asyncio.Event()
        runner = ChatRunner(agent)
        turn = runner.start("friend", "hello")
        with pytest.raises(TurnBusy):
            runner.start("friend", "again")
        runner.start("andrew", "independent")  # other users aren't blocked

        # client reads nothing and goes away (phone backgrounded)
        backend.gate.set()
        await runner.wait_idle()
        assert turn.done
        assert agent.history("friend")[-1]["assistant"] == "part one part two"

        # re-attach from the second event
        seen = [e async for e in runner.events(turn, 1) if e is not None]
        assert seen[0] == {"type": "text-delta", "text": "part two"}
        assert seen[-1]["type"] == "result"
        assert runner.get("andrew", turn.id) is None  # bound to its user
    asyncio.run(go())


# ------------------------------------------------------------------ HTTP ----
def test_chat_endpoint_streams_ndjson_and_saves_history(env, tmp_path, fernet_key) -> None:
    import httpx2

    from garmin_mcp.config import Settings
    from garmin_mcp.webapp import build_app_routes
    from garmin_mcp.webauth import WebAuth
    from starlette.applications import Starlette

    settings = Settings(public_base_url="http://localhost:8001", data_dir=tmp_path,
                        app_enabled=True, mcp_enabled=False)
    agent, backend = env.agent([
        ([], [tool_block("recent_activities", {"days": 3})], "tool_use"),
        (["Solid ", "few days."], [], "end_turn"),
    ])
    runner = ChatRunner(agent)
    auth = WebAuth(settings, env.store, "", dev_user="friend")
    app = Starlette(routes=build_app_routes(settings, auth, env.tokens, None, None, runner))
    hdrs = {"origin": "http://localhost:8001", "content-type": "application/json"}

    async def go() -> None:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                      base_url="http://localhost:8001") as c:
            r = await c.post("/app/api/chat", headers=hdrs, content=b'{"message": "how was it?"}')
            assert r.headers["content-type"].startswith("application/x-ndjson")
            events = [json.loads(x) for x in r.text.splitlines() if x.strip()]
            assert [e["type"] for e in events] == [
                "tool-call-start", "tool-call-result", "text-delta", "text-delta", "usage", "result"]
            turn_id = events[-1]["response"]["turnId"]
            assert r.headers["x-turn-id"] == turn_id
            await runner.wait_idle()
            page = (await c.get("/app/api/messages")).json()
            assert page["turns"][-1]["id"] == turn_id
            assert page["turns"][-1]["user"] == "how was it?"
            assert "assistant" not in page["turns"][-1]  # UI gets parts only
            again = await c.get(f"/app/api/chat/{turn_id}/events", params={"from": 4})
            assert [json.loads(x)["type"] for x in again.text.splitlines() if x.strip()] == [
                "usage", "result"]
            assert (await c.post("/app/api/chat", headers={"content-type": "application/json"},
                                 content=b'{"message": "x"}')).status_code == 403
            assert (await c.post("/app/api/chat/clear", headers=hdrs, content=b"{}")).json()["ok"]
            assert (await c.get("/app/api/messages")).json()["turns"] == []
    asyncio.run(go())
