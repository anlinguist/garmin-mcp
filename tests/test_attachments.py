"""Chat attachments (sniffing, guards, model blocks vs. history placeholders) and Stop."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx2
import pytest
from starlette.applications import Starlette

from garmin_mcp.attachments import AttachmentError, AttachmentStore, sniff
from garmin_mcp.config import Settings
from garmin_mcp.webapp import build_app_routes
from garmin_mcp.webauth import WebAuth
from garmin_mcp.webchat import ChatRunner
from tests.test_agent import env  # noqa: F401  (fixture)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
BASE = "http://localhost:8001"
HDRS = {"origin": BASE, "content-type": "application/json"}


@pytest.mark.parametrize("data,name,kind", [
    (PNG, "x.png", "image"), (b"\xff\xd8\xff\xe0rest", "p.jpg", "image"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "w.webp", "image"), (PDF, "plan.pdf", "pdf"),
    ("week 1: easy".encode(), "plan.md", "text"),
])
def test_sniff_by_content(data, name, kind) -> None:
    assert sniff(data, name)[0] == kind


@pytest.mark.parametrize("data,name", [
    (b"MZ\x90\x00 windows exe", "photo.png"),       # extension lies
    (b"<svg onload=alert(1)>", "logo.svg"),          # SVG can carry script
    (b"\xff\xfe\x00bad", "notes.txt"),               # not UTF-8
])
def test_sniff_rejects(data, name) -> None:
    with pytest.raises(AttachmentError):
        sniff(data, name)


def _app(env, tmp_path, user="friend", backend_steps=None):
    settings = Settings(public_base_url=BASE, data_dir=tmp_path, app_enabled=True, mcp_enabled=False)
    agent, backend = env.agent(backend_steps or [])
    runner = ChatRunner(agent)
    auth = WebAuth(settings, env.store, "", dev_user=user)
    app = Starlette(routes=build_app_routes(settings, auth, env.tokens, None, None, runner))
    return app, agent, backend, runner


def _client(app) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE)


def test_upload_guards_and_owner_only(env, tmp_path) -> None:
    app, agent, _, _ = _app(env, tmp_path)
    other, *_ = _app(env, tmp_path, user="andrew")

    async def go() -> None:
        async with _client(app) as c:
            assert (await c.post("/app/api/attachments?name=x.png", content=PNG)).status_code == 403
            r = await c.post("/app/api/attachments?name=x.png", content=PNG, headers={"origin": BASE})
            assert r.status_code == 201
            meta = r.json()
            assert meta["kind"] == "image" and meta["mime"] == "image/png"
            got = await c.get(f"/app/api/attachments/{meta['id']}")
            assert got.content == PNG and got.headers["x-content-type-options"] == "nosniff"
            bad = await c.post("/app/api/attachments?name=a.png", content=b"MZ evil", headers={"origin": BASE})
            assert bad.status_code == 400
            big = await c.post("/app/api/attachments?name=a.pdf", headers={"origin": BASE},
                               content=b"%PDF-" + b"0" * (11 * 1024 * 1024))
            assert big.status_code == 413
            assert (await c.get("/app/api/attachments/../../etc")).status_code == 404
        async with _client(other) as c:
            assert (await c.get(f"/app/api/attachments/{meta['id']}")).status_code == 404
    asyncio.run(go())


def test_attachment_reaches_model_once_and_stays_in_history(env, tmp_path) -> None:
    app, agent, backend, runner = _app(env, tmp_path, backend_steps=[
        (["Nice plan."], [], "end_turn"), (["Sure."], [], "end_turn")])
    thread = agent.coach.create_thread("friend")["id"]

    async def go() -> None:
        async with _client(app) as c:
            meta = (await c.post("/app/api/attachments?name=plan.pdf", content=PDF,
                                 headers={"origin": BASE})).json()
            body = {"message": "", "thread_id": thread, "attachments": [meta["id"]]}
            r = await c.post("/app/api/chat", headers=HDRS, content=json.dumps(body).encode())
            assert r.status_code == 200
            await runner.wait_idle()
            first = backend.calls[0][-1]["content"]
            assert first[0]["type"] == "document" and first[0]["source"]["media_type"] == "application/pdf"
            assert "(see attached)" in first[-1]["text"]
            assert first[-1]["text"].endswith(f"[Attached this turn: plan.pdf (id {meta['id']})]")
            body = {"message": "and next week?", "thread_id": thread}
            await c.post("/app/api/chat", headers=HDRS, content=json.dumps(body).encode())
            await runner.wait_idle()
            ctx = backend.calls[1]
            assert ctx[0]["content"] == f"(see attached)\n[pdf: plan.pdf, id {meta['id']}]"  # placeholder
            assert not any(isinstance(m["content"], list) for m in ctx[:-1])
            page = (await c.get(f"/app/api/threads/{thread}/messages")).json()
            assert page["turns"][0]["attachments"] == [
                {"id": meta["id"], "name": "plan.pdf", "kind": "pdf", "mime": "application/pdf",
                 "size": len(PDF)}]
            too_many = {"message": "x", "thread_id": thread, "attachments": [meta["id"]] * 5}
            assert (await c.post("/app/api/chat", headers=HDRS,
                                 content=json.dumps(too_many).encode())).status_code == 400
    asyncio.run(go())


def test_stop_keeps_partial_reply(env, tmp_path) -> None:
    class Slow:
        model = "slow"

        async def turn(self, *, system, tools, messages, on_text):
            await on_text("Here's the start")
            await asyncio.sleep(30)

    app, agent, _, runner = _app(env, tmp_path)
    agent._backend = Slow()
    thread = agent.coach.create_thread("friend")["id"]

    async def go() -> None:
        turn = runner.start("friend", "long question", thread_id=thread)
        await asyncio.sleep(0.05)
        async with _client(app) as c:
            r = await c.post(f"/app/api/chat/{turn.id}/cancel", headers=HDRS, content=b"{}")
            assert r.json() == {"ok": True}
        await runner.wait_idle()
        saved = agent.coach.turns("friend", thread)[-1]
        assert saved["assistant"] == "Here's the start …(stopped)"
        assert turn.events[-1]["type"] == "result"
    asyncio.run(go())


def test_store_rejects_bad_ids(tmp_path, fernet_key) -> None:
    from garmin_mcp.appstore import AppStore
    s = AttachmentStore(AppStore(tmp_path, fernet_key))
    assert s.load("friend", "nope") is None
    with pytest.raises(AttachmentError):
        s.for_message("friend", ["at0000000000000000"])


def test_read_file_reopens_own_uploads_only(env, tmp_path) -> None:
    from tests.test_agent import go, tool_block
    app, agent, backend, runner = _app(env, tmp_path)
    mine = agent.attachments.save("friend", "plan.pdf", PDF)
    theirs = agent.attachments.save("andrew", "x.pdf", PDF)
    backend.steps = [
        ([], [tool_block("read_file", {"attachment_id": mine["id"]}),
              tool_block("read_file", {"attachment_id": theirs["id"]}, 1)], "tool_use"),
        (["Tuesdays are intervals."], [], "end_turn"),
    ]
    events = []
    go(agent, "friend", "which day is intervals?", events.append)
    results = backend.calls[1][-1]["content"]
    assert results[0]["content"][1]["type"] == "document"  # the original PDF went back
    assert results[0]["content"][1]["source"]["media_type"] == "application/pdf"
    assert results[1]["is_error"] and "not found" in results[1]["content"]
    shown = next(e for e in events if e["type"] == "tool-call-result")["result"]
    assert shown == {"file": "plan.pdf", "kind": "pdf"}  # UI gets a small summary, not the bytes


def test_messages_from_start_for_the_peek(env, tmp_path) -> None:
    app, agent, _, _ = _app(env, tmp_path)
    thread = agent.coach.create_thread("friend")["id"]
    for i in range(5):
        agent.coach.append_turn("friend", thread, {"id": f"t{i}", "ts": i, "kind": "chat",
                                                   "user": f"q{i}", "assistant": f"a{i}", "parts": []})

    async def go() -> None:
        async with _client(app) as c:
            first = (await c.get(f"/app/api/threads/{thread}/messages?from=start&limit=2")).json()
            last = (await c.get(f"/app/api/threads/{thread}/messages?limit=2")).json()
            assert [t["user"] for t in first["turns"]] == ["q0", "q1"] and first["has_more"]
            assert [t["user"] for t in last["turns"]] == ["q3", "q4"]
    asyncio.run(go())
