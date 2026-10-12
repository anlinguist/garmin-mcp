"""Detached chat turns for Rabbit.

A turn runs as its own task, independent of the HTTP request that started it,
so a phone that backgrounds the app (and drops the stream) still gets the
reply saved to history; the client can re-attach with ``events?from=N``.

One turn at a time per user, and at most ``max_concurrent`` turns server-wide
(e2-micro). Finished turns are kept briefly for re-attachment, then dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .agent import Agent

KEEP_FINISHED = 300
HEARTBEAT = 15.0


class TurnBusy(Exception):
    def __init__(self, turn_id: str) -> None:
        super().__init__(turn_id)
        self.turn_id = turn_id


@dataclass
class Turn:
    id: str
    user_id: str
    created: float
    events: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    finished_at: float = 0.0
    record: dict[str, Any] | None = None  # the stored turn, once finished
    _subs: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        for q in self._subs:
            q.put_nowait(event)

    def close(self) -> None:
        self.done = True
        self.finished_at = time.time()
        self._finished.set()
        for q in self._subs:
            q.put_nowait(None)

    async def wait(self) -> dict[str, Any] | None:
        await self._finished.wait()
        return self.record


class ChatRunner:
    def __init__(self, agent: Agent, *, max_concurrent: int = 2) -> None:
        self.agent = agent
        self._max = max_concurrent
        self._sem: asyncio.Semaphore | None = None
        self._turns: dict[str, Turn] = {}
        self._active: dict[str, str] = {}  # user_id -> running turn id
        self._tasks: set[asyncio.Task] = set()

    def _prune(self) -> None:
        now = time.time()
        for tid, t in list(self._turns.items()):
            if t.done and now - t.finished_at > KEEP_FINISHED:
                del self._turns[tid]

    def active(self, user_id: str) -> Turn | None:
        tid = self._active.get(user_id)
        return self._turns.get(tid) if tid else None

    def get(self, user_id: str, turn_id: str) -> Turn | None:
        t = self._turns.get(turn_id)
        return t if t is not None and t.user_id == user_id else None

    def start(self, user_id: str, text: str, *, kind: str = "chat") -> Turn:
        self._prune()
        running = self.active(user_id)
        if running is not None and not running.done:
            raise TurnBusy(running.id)
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max)
        turn = Turn("t_" + secrets.token_urlsafe(9), user_id, time.time())
        self._turns[turn.id] = turn
        self._active[user_id] = turn.id
        sem = self._sem

        async def go() -> None:
            try:
                async with sem:
                    turn.record = await self.agent.run(user_id, text, turn.emit,
                                                       turn_id=turn.id, kind=kind)
            except Exception as e:  # agent.run handles its own errors; this is a backstop
                turn.emit({"type": "result", "response": {
                    "status": "error", "turnId": turn.id,
                    "error": f"Internal error ({type(e).__name__})", "steps": 0}})
            finally:
                turn.close()
                if self._active.get(user_id) == turn.id:
                    del self._active[user_id]

        task = asyncio.get_running_loop().create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return turn

    async def events(self, turn: Turn, start: int = 0) -> AsyncIterator[dict[str, Any] | None]:
        """Replay events from ``start``, then follow live ones. Yields None as a heartbeat."""
        q: asyncio.Queue = asyncio.Queue()
        for e in turn.events[start:]:
            q.put_nowait(e)
        if turn.done:
            q.put_nowait(None)  # end marker
        else:
            turn._subs.append(q)
        try:
            while True:
                try:
                    e = await asyncio.wait_for(q.get(), timeout=HEARTBEAT)
                except TimeoutError:
                    yield None
                    continue
                if e is None:
                    return
                yield e
        finally:
            with contextlib.suppress(ValueError):
                turn._subs.remove(q)

    async def wait_idle(self) -> None:
        """Tests: wait for running turns to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

