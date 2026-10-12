"""HTTP routes for the Rabbit web app, mounted under /app on the MCP server's app.

Every /app/api route goes through ``_api``: Host check, session -> user_id
(never from the request body), Origin + JSON content-type check on mutating
calls, a body size cap, and a generic error for anything unexpected.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from .config import Settings
from .logs import log_event
from .tokens import TokenStore, TokenStoreError
from .webauth import WebAuth
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
                     push: WebPush | None) -> list[BaseRoute]:
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
        return JSONResponse({
            "user_id": user_id,
            "profile": auth.profile(user_id),
            "garmin_connected": connected,
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
    ]
    if (WEB_DIST / "index.html").is_file():
        routes.append(Mount("/app", app=_Static(directory=WEB_DIST, html=True)))
    else:
        routes.append(Route("/app/{path:path}", not_built, methods=["GET"]))
    return routes
