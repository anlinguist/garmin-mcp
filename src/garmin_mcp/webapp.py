"""HTTP routes for the Rabbit web app, mounted under /app on the MCP server's app.

Every /app/api route goes through ``_api``: Host check, session -> user_id
(never from the request body), Origin + JSON content-type check on mutating
calls, a body size cap, and a generic error for anything unexpected.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .appstore import AppStore
from .coaching import CoachError
from .config import Settings
from .garminlogin import Busy, Cooldown, LoginManager
from .logs import log_event
from .tokens import TokenStore, TokenStoreError
from .webauth import WebAuth
from .webchat import ChatRunner, Turn, TurnBusy
from .webpush import WebPush, parse_subscription

logger = logging.getLogger(__name__)

WEB_DIST = Path(__file__).parent / "web_dist"
MAX_BODY = 16 * 1024

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",  # React/Radix set inline style attributes
    "img-src 'self' data: https://lh3.googleusercontent.com",  # Google profile photos
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

Handler = Callable[[Request, str], Awaitable[Response]]


class _Static(StaticFiles):
    """Built frontend: hashed assets cache forever, everything else revalidates."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        resp = await super().get_response(path, scope)
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Content-Security-Policy"] = CSP
        return resp


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def read_json(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise ValueError("body too large")
    return json.loads(raw or b"null")


def build_app_routes(settings: Settings, auth: WebAuth, token_store: TokenStore,
                     push: WebPush | None, logins: LoginManager | None = None,
                     chat: ChatRunner | None = None,
                     *, fake_garmin: bool = False,
                     dev: dict[str, Callable[[str], Any]] | None = None,
                     default_tz: str = "America/Denver") -> list[BaseRoute]:
    local = settings.public_host.split(":")[0] in {"localhost", "127.0.0.1"}

    def host_ok(request: Request) -> bool:
        host = request.headers.get("host", "")
        if host == settings.public_host:
            return True
        return local and host.split(":")[0] in {"localhost", "127.0.0.1"}

    def _api(handler: Handler, *, mutating: bool) -> Callable[[Request], Awaitable[Response]]:
        async def endpoint(request: Request) -> Response:
            if not host_ok(request):
                return _err(400, "bad host")
            user_id = auth.user_for(request)
            if user_id is None:
                return _err(401, "not signed in")
            if mutating and not auth.check_origin(request):
                return _err(403, "bad origin")
            try:
                resp = await handler(request, user_id)
            except (ValueError, json.JSONDecodeError):
                return _err(400, "bad request")
            except Exception as e:
                log_event(logger, logging.ERROR, "app_unexpected_error",
                          path=request.url.path, error=type(e).__name__)
                return _err(500, f"Internal error ({type(e).__name__})")
            resp.headers["Cache-Control"] = "no-store"
            return resp
        return endpoint

    # ---------------------------------------------------------------- api --
    async def me(request: Request, user_id: str) -> Response:
        try:
            connected = token_store.load(user_id) is not None
        except TokenStoreError:
            connected = False
        cd = logins.cooldown(user_id) if logins else None
        return JSONResponse({
            "user_id": user_id,
            "profile": auth.profile(user_id),
            "settings": _settings_view(user_id),
            "dev": {"fake_garmin": fake_garmin, "tools": sorted(dev) if dev else []},
            "athlete": chat.agent.coach.athlete(user_id) if chat else {},
            "goal": chat.agent.coach.active_goal(user_id) if chat else None,
            "chat": {"available": chat is not None,
                     "active_turn": (t.id if chat and (t := chat.active(user_id)) else None)},
            "garmin_connected": connected,
            "garmin": {
                "connected": connected,
                "can_connect": logins is not None,
                "cooldown_until": int(cd[0]) if cd else None,
                "cooldown_scope": cd[1] if cd else None,
                "fake": fake_garmin,
            },
            "push": {
                "available": push is not None,
                "public_key": push.public_key if push else None,
                "subscriptions": push.count(user_id) if push else 0,
            },
        })

    async def push_subscribe(request: Request, user_id: str) -> Response:
        if push is None:
            return _err(503, "push not configured")
        sub = parse_subscription(await read_json(request))
        if sub is None:
            return _err(400, "invalid subscription")
        n = push.subscribe(user_id, sub)
        return JSONResponse({"ok": True, "subscriptions": n})

    async def push_unsubscribe(request: Request, user_id: str) -> Response:
        if push is None:
            return _err(503, "push not configured")
        body = await read_json(request)
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        if not isinstance(endpoint, str):
            return _err(400, "endpoint required")
        push.unsubscribe(user_id, endpoint)
        return JSONResponse({"ok": True})

    async def push_test(request: Request, user_id: str) -> Response:
        if push is None:
            return _err(503, "push not configured")
        results = await anyio.to_thread.run_sync(
            lambda: push.send(user_id, "Rabbit", "Notifications are working.")
        )
        return JSONResponse({"results": [
            {"host": r.endpoint_host, "status": r.status, "removed": r.removed} for r in results
        ]})

    # ------------------------------------------------------- Garmin login --
    def _str(body: Any, key: str, max_len: int) -> str:
        v = body.get(key) if isinstance(body, dict) else None
        if not isinstance(v, str) or not v.strip() or len(v) > max_len:
            raise ValueError(key)
        return v

    def _cooldown_resp(e: Cooldown) -> JSONResponse:
        return JSONResponse({"state": "cooldown", "cooldown_until": int(e.until),
                             "cooldown_scope": e.scope}, status_code=429)

    async def garmin_login(request: Request, user_id: str) -> Response:
        if logins is None:
            return _err(503, "Garmin connect not available")
        body = await read_json(request)
        email = _str(body, "email", 254).strip()
        password = _str(body, "password", 256)
        del body
        try:
            job = logins.start(user_id, email, password)
        except Cooldown as e:
            return _cooldown_resp(e)
        except Busy:
            return _err(409, "Another Garmin login is in progress. Try again in a minute.")
        finally:
            del password
        return JSONResponse(job.public(), status_code=202)

    async def garmin_mfa(request: Request, user_id: str) -> Response:
        if logins is None:
            return _err(503, "Garmin connect not available")
        body = await read_json(request)
        job_id = _str(body, "job_id", 64)
        code = _str(body, "code", 12).strip()
        if not code.isdigit():
            raise ValueError("code")
        try:
            job = logins.submit_mfa(user_id, job_id, code)
        except KeyError:
            return _err(404, "login not found")
        except Busy:
            return _err(409, "Another Garmin login is in progress. Try again in a minute.")
        return JSONResponse(job.public(), status_code=202)

    async def garmin_login_status(request: Request, user_id: str) -> Response:
        if logins is None:
            return _err(503, "Garmin connect not available")
        try:
            job = logins.status(user_id, request.path_params["job_id"])
        except KeyError:
            return _err(404, "login not found")
        out = job.public()
        if job.state == "error":
            cd = logins.cooldown(user_id)
            out["cooldown_until"] = int(cd[0]) if cd else None
            out["cooldown_scope"] = cd[1] if cd else None
        return JSONResponse(out)

    async def garmin_disconnect(request: Request, user_id: str) -> Response:
        if logins is None:
            return _err(503, "Garmin connect not available")
        logins.disconnect(user_id, token_store)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ settings --
    store: AppStore | None = chat.agent._store if chat is not None else None

    def _settings_view(user_id: str) -> dict[str, Any]:
        p = store.user_doc(user_id, "profile").load() if store else {}
        return {"timezone": p.get("timezone") or default_tz, "timezone_set": bool(p.get("timezone")),
                "brief_time": p.get("brief_time") or "06:30",
                "brief_enabled": p.get("brief_enabled", True),
                "workout_feedback": p.get("workout_feedback", True),
                "has_notes": bool(p.get("notes"))}

    async def put_settings(request: Request, user_id: str) -> Response:
        if store is None:
            return _err(503, "Settings aren't available on this server.")
        body = await read_json(request)
        if not isinstance(body, dict):
            raise ValueError("body")
        upd: dict[str, Any] = {}
        if "timezone" in body:
            tz = body["timezone"]
            try:
                if not isinstance(tz, str) or len(tz) > 64:
                    raise ValueError
                ZoneInfo(tz)
            except (ValueError, ZoneInfoNotFoundError):
                return _err(400, "unknown timezone")
            upd["timezone"] = tz
        if "brief_time" in body:
            bt = body["brief_time"]
            if not isinstance(bt, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", bt):
                return _err(400, "brief_time must be HH:MM")
            upd["brief_time"] = bt
        for key in ("brief_enabled", "workout_feedback"):
            if key in body:
                if not isinstance(body[key], bool):
                    return _err(400, f"{key} must be true or false")
                upd[key] = body[key]
        store.user_doc(user_id, "profile").update(lambda p: p.update(upd))
        return JSONResponse(_settings_view(user_id))

    async def dev_action(request: Request, user_id: str) -> Response:
        name = request.path_params["name"]
        if not dev or name not in dev:
            return _err(404, "not found")
        out = await anyio.to_thread.run_sync(dev[name], user_id)
        return JSONResponse({"ok": True, "result": out})

    # ---------------------------------------------------------------- chat --
    def _ndjson(turn: Turn, start: int) -> StreamingResponse:
        assert chat is not None

        async def gen() -> AsyncIterator[bytes]:
            async for e in chat.events(turn, start):
                yield b"\n" if e is None else (json.dumps(e, default=str) + "\n").encode()
        return StreamingResponse(gen(), media_type="application/x-ndjson",
                                 headers={"X-Accel-Buffering": "no", "X-Turn-Id": turn.id})

    def _coach():
        assert chat is not None
        return chat.agent.coach

    def _thread_or_404(user_id: str, thread_id: Any) -> dict[str, Any] | None:
        return _coach().get_thread(user_id, thread_id) if isinstance(thread_id, str) else None

    async def chat_send(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        message = _str(body, "message", 4000).strip()
        thread = _thread_or_404(user_id, body.get("thread_id"))
        if thread is None:
            return _err(404, "thread not found")
        try:
            turn = chat.start(user_id, message, thread_id=thread["id"])
        except TurnBusy as e:
            return JSONResponse({"error": "A reply is already in progress.",
                                 "turn_id": e.turn_id}, status_code=409)
        return _ndjson(turn, 0)

    async def chat_events(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        turn = chat.get(user_id, request.path_params["turn_id"])
        if turn is None:
            return _err(404, "turn not found")
        try:
            start = max(0, int(request.query_params.get("from", "0")))
        except ValueError:
            start = 0
        return _ndjson(turn, start)

    def _thread_view(t: dict[str, Any]) -> dict[str, Any]:
        return {k: t.get(k) for k in ("id", "kind", "title", "goal_id", "created", "updated", "seed")}

    async def threads_list(request: Request, user_id: str) -> Response:
        if chat is None:
            return JSONResponse({"threads": [], "goal": None})
        coach = _coach()
        coach.migrate_legacy(user_id)
        return JSONResponse({"goal": coach.active_goal(user_id),
                             "threads": [_thread_view(t) for t in coach.threads(user_id)]})

    async def thread_create(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        body = body if isinstance(body, dict) else {}
        seed = body.get("from")
        if seed is not None and not (isinstance(seed, dict) and isinstance(seed.get("thread_id"), str)
                                     and isinstance(seed.get("turn_id"), str)):
            raise ValueError("from")
        title = body.get("title")
        try:
            t = _coach().create_thread(user_id, title=title if isinstance(title, str) else None,
                                       seed=seed)
        except CoachError as e:
            return _err(400, str(e))
        return JSONResponse(_thread_view(t), status_code=201)

    async def thread_update(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        try:
            t = _coach().rename_thread(user_id, request.path_params["thread_id"],
                                       _str(body, "title", 80))
        except CoachError as e:
            return _err(404, str(e))
        return JSONResponse(_thread_view(t))

    async def thread_delete(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        thread = _thread_or_404(user_id, request.path_params["thread_id"])
        if thread is None:
            return _err(404, "thread not found")
        active = chat.active(user_id)
        if active is not None and not active.done and active.thread_id == thread["id"]:
            return _err(409, "Wait for the current reply to finish.")
        try:
            if thread["kind"] == "coach":
                _coach().clear_thread(user_id, thread["id"])
            else:
                _coach().delete_thread(user_id, thread["id"])
        except CoachError as e:
            return _err(400, str(e))
        return JSONResponse({"ok": True})

    async def messages(request: Request, user_id: str) -> Response:
        if chat is None:
            return JSONResponse({"turns": []})
        thread = _thread_or_404(user_id, request.path_params["thread_id"])
        if thread is None:
            return _err(404, "thread not found")
        try:
            limit = min(100, max(1, int(request.query_params.get("limit", "60"))))
        except ValueError:
            limit = 60
        turns = _coach().turns(user_id, thread["id"])
        before = request.query_params.get("before")
        if before:
            idx = next((i for i, t in enumerate(turns) if t.get("id") == before), len(turns))
            turns = turns[:idx]
        page = turns[-limit:]
        keys = ("id", "ts", "kind", "label", "user", "parts", "attachments", "seed_from")
        return JSONResponse({"thread": _thread_view(thread),
                             "turns": [{k: t.get(k) for k in keys if k in t} for t in page],
                             "has_more": len(turns) > len(page)})

    # ------------------------------------------------------ goal / athlete --
    async def goals_get(request: Request, user_id: str) -> Response:
        if chat is None:
            return JSONResponse({"active": None, "goals": []})
        return JSONResponse(_coach().goals(user_id))

    async def goal_create(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        if not isinstance(body, dict):
            raise ValueError("body")
        try:
            return JSONResponse(_coach().create_goal(user_id, body), status_code=201)
        except CoachError as e:
            return _err(400, str(e))

    async def goal_update(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        if not isinstance(body, dict):
            raise ValueError("body")
        try:
            return JSONResponse(_coach().update_goal(user_id, request.path_params["goal_id"], body))
        except CoachError as e:
            return _err(400, str(e))

    async def goal_archive(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        try:
            _coach().archive_goal(user_id, request.path_params["goal_id"])
        except CoachError as e:
            return _err(404, str(e))
        return JSONResponse({"ok": True})

    async def athlete_put(request: Request, user_id: str) -> Response:
        if chat is None:
            return _err(503, "Chat isn't configured on this server.")
        body = await read_json(request)
        if not isinstance(body, dict):
            raise ValueError("body")
        try:
            return JSONResponse(_coach().update_athlete(user_id, body))
        except CoachError as e:
            return _err(400, str(e))

    async def diag_stream(request: Request, user_id: str) -> Response:
        """Phase 0: check NDJSON arrives incrementally through Cloudflare + Caddy."""
        async def gen() -> AsyncIterator[bytes]:
            for i in range(10):
                yield (json.dumps({"type": "tick", "n": i}) + "\n").encode()
                await anyio.sleep(0.5)
            yield (json.dumps({"type": "done"}) + "\n").encode()
        return StreamingResponse(gen(), media_type="application/x-ndjson",
                                 headers={"X-Accel-Buffering": "no"})

    # --------------------------------------------------------------- auth --
    async def google_start(request: Request) -> Response:
        if not host_ok(request):
            return _err(400, "bad host")
        return await auth.start(request)

    async def google_callback(request: Request) -> Response:
        if not host_ok(request):
            return _err(400, "bad host")
        return await auth.callback(request)

    async def logout(request: Request) -> Response:
        if not host_ok(request) or not auth.check_origin(request):
            return _err(403, "bad origin")
        resp = JSONResponse({"ok": True})
        auth.logout(request, resp)
        return resp

    async def app_root(request: Request) -> Response:
        return RedirectResponse("/app/", status_code=308)

    async def not_built(request: Request) -> Response:
        return Response("Frontend not built: run `bun run build` in web/.", status_code=503,
                        media_type="text/plain")

    routes: list[BaseRoute] = [
        Route("/app", app_root, methods=["GET"]),
        Route("/app/auth/google/start", google_start, methods=["GET"]),
        Route("/app/auth/google/callback", google_callback, methods=["GET"]),
        Route("/app/auth/logout", logout, methods=["POST"]),
        Route("/app/api/me", _api(me, mutating=False), methods=["GET"]),
        Route("/app/api/push/subscribe", _api(push_subscribe, mutating=True), methods=["POST"]),
        Route("/app/api/push/unsubscribe", _api(push_unsubscribe, mutating=True),
              methods=["POST"]),
        Route("/app/api/push/test", _api(push_test, mutating=True), methods=["POST"]),
        Route("/app/api/diag/stream", _api(diag_stream, mutating=True), methods=["POST"]),
        Route("/app/api/settings", _api(put_settings, mutating=True), methods=["PUT"]),
        Route("/app/api/dev/{name}", _api(dev_action, mutating=True), methods=["POST"]),
        Route("/app/api/chat", _api(chat_send, mutating=True), methods=["POST"]),
        Route("/app/api/chat/{turn_id}/events", _api(chat_events, mutating=False),
              methods=["GET"]),
        Route("/app/api/threads", _api(threads_list, mutating=False), methods=["GET"]),
        Route("/app/api/threads", _api(thread_create, mutating=True), methods=["POST"]),
        Route("/app/api/threads/{thread_id}", _api(thread_update, mutating=True),
              methods=["PATCH"]),
        Route("/app/api/threads/{thread_id}", _api(thread_delete, mutating=True),
              methods=["DELETE"]),
        Route("/app/api/threads/{thread_id}/messages", _api(messages, mutating=False),
              methods=["GET"]),
        Route("/app/api/goals", _api(goals_get, mutating=False), methods=["GET"]),
        Route("/app/api/goals", _api(goal_create, mutating=True), methods=["POST"]),
        Route("/app/api/goals/{goal_id}", _api(goal_update, mutating=True), methods=["PATCH"]),
        Route("/app/api/goals/{goal_id}/archive", _api(goal_archive, mutating=True),
              methods=["POST"]),
        Route("/app/api/athlete", _api(athlete_put, mutating=True), methods=["PUT"]),
        Route("/app/api/garmin/login", _api(garmin_login, mutating=True), methods=["POST"]),
        Route("/app/api/garmin/login/{job_id}", _api(garmin_login_status, mutating=False),
              methods=["GET"]),
        Route("/app/api/garmin/mfa", _api(garmin_mfa, mutating=True), methods=["POST"]),
        Route("/app/api/garmin/disconnect", _api(garmin_disconnect, mutating=True),
              methods=["POST"]),
    ]
    if (WEB_DIST / "index.html").is_file():
        routes.append(Mount("/app", app=_Static(directory=WEB_DIST, html=True)))
    else:
        routes.append(Route("/app/{path:path}", not_built, methods=["GET"]))
    return routes
