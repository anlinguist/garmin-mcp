"""Rabbit's coaching state: athlete profile, goals, threads, and per-thread history.

Layers of context (all per user, Fernet-encrypted via AppStore):

* **Athlete profile** (``profile.athlete``): durable across goals.
* **Goal**: one active at a time; creating a new one archives the old. Each
  goal carries its own coach notes (what Rabbit has learned this block).
* **Threads**: each goal (or ``"none"`` before a goal exists) has one pinned
  **coach** thread, where briefs and workout notes land, plus any number of
  **topic** threads. Starting a topic thread drops a link card into the coach
  thread, so the coach log reads as the goal's timeline; the conversation
  itself lives in the topic thread.

Turn records (one doc per thread, ``conv-<id>``)::

    {"id", "ts", "kind": "chat" | "proactive" | "seed" | "link",
     "label"?: "brief" | "workout", "user", "assistant", "parts", "attachments"?}

``link`` turns are UI-only and never reach the model.
"""

from __future__ import annotations

import re
import secrets
import time
from datetime import date, datetime
from typing import Any

from .appstore import AppStore

NO_GOAL = "none"
MAX_THREADS = 300
STORED_TURNS = 200

DISTANCES = ("sprint triathlon", "olympic triathlon", "70.3", "140.6", "5k", "10k",
             "half marathon", "marathon", "ultra", "century ride", "other")

_ATHLETE_TEXT = {"experience": 300, "training_days": 200, "injuries": 500,
                 "preferences": 500}
_GOAL_TEXT = {"title": 80, "event": 120, "distance": 40, "target": 160, "priorities": 600,
              "plan": 6000, "notes": 2000}
PLAN_CONTEXT_CHARS = 4000
MAX_PLAN_FILES = 10


class CoachError(ValueError):
    """Validation error whose message is safe to show the user."""


def _clean_text(v: Any, limit: int, field: str) -> str:
    if not isinstance(v, str):
        raise CoachError(f"{field} must be text")
    return v.strip()[:limit]


def _new_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(6)}"


def training_phase(race: date, today: date) -> tuple[int, str]:
    """(weeks to race, rough phase). Generic endurance periodization."""
    days = (race - today).days
    weeks = days // 7
    if days < 0:
        return weeks, "post-race"
    if days <= 7:
        return weeks, "race week"
    if weeks < 3:
        return weeks, "taper"
    if weeks < 8:
        return weeks, "peak"
    if weeks < 16:
        return weeks, "build"
    return weeks, "base"


class CoachStore:
    def __init__(self, store: AppStore, *, clock: Any = time.time) -> None:
        self._s = store
        self._clock = clock

    # --------------------------------------------------------- athlete -----
    def athlete(self, user_id: str) -> dict[str, Any]:
        return dict(self._s.user_doc(user_id, "profile").load().get("athlete", {}))

    def update_athlete(self, user_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        upd: dict[str, Any] = {}
        for k, v in patch.items():
            if k in _ATHLETE_TEXT:
                upd[k] = _clean_text(v, _ATHLETE_TEXT[k], k)
            elif k == "units":
                if v not in ("metric", "imperial"):
                    raise CoachError("units must be metric or imperial")
                upd[k] = v
            elif k in ("weekly_hours", "age"):
                if v in (None, ""):
                    upd[k] = None
                    continue
                if not isinstance(v, int | float) or isinstance(v, bool) or not 0 <= v <= 120:
                    raise CoachError(f"{k} must be a number")
                upd[k] = round(float(v), 1) if k == "weekly_hours" else int(v)
            else:
                raise CoachError(f"unknown profile field {k!r}")

        def apply(p: dict[str, Any]) -> dict[str, Any]:
            a = p.setdefault("athlete", {})
            a.update(upd)
            return dict(a)
        return self._s.user_doc(user_id, "profile").update(apply)

    # ----------------------------------------------------------- goals -----
    def _goals_doc(self, user_id: str):
        return self._s.user_doc(user_id, "goals")

    def goals(self, user_id: str) -> dict[str, Any]:
        d = self._goals_doc(user_id).load()
        goals = sorted(d.get("goals", {}).values(), key=lambda g: g["created"], reverse=True)
        return {"active": d.get("active"), "goals": goals}

    def active_goal(self, user_id: str) -> dict[str, Any] | None:
        d = self._goals_doc(user_id).load()
        gid = d.get("active")
        return d.get("goals", {}).get(gid) if gid else None

    def _validate_goal(self, fields: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in fields.items():
            if k in _GOAL_TEXT:
                out[k] = _clean_text(v, _GOAL_TEXT[k], k)
            elif k == "date":
                if v in (None, ""):
                    out[k] = None
                    continue
                try:
                    out[k] = date.fromisoformat(str(v)).isoformat()
                except ValueError:
                    raise CoachError("date must be YYYY-MM-DD") from None
            else:
                raise CoachError(f"unknown goal field {k!r}")
        if not partial and not out.get("title"):
            out["title"] = out.get("event") or out.get("distance") or "My goal"
        return out

    def create_goal(self, user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        g = self._validate_goal(fields, partial=False)
        now = int(self._clock())
        goal = {"id": _new_id("g"), "created": now, "archived_at": None, "notes": "",
                "title": g.get("title"), "event": g.get("event", ""), "date": g.get("date"),
                "distance": g.get("distance", ""), "target": g.get("target", ""),
                "priorities": g.get("priorities", ""), "plan": g.get("plan", ""),
                "plan_files": []}

        def add(d: dict[str, Any]) -> bool:
            goals = d.setdefault("goals", {})
            prev = d.get("active")
            if prev and prev in goals:
                goals[prev]["archived_at"] = now
            goals[goal["id"]] = goal
            d["active"] = goal["id"]
            return not prev
        first = self._goals_doc(user_id).update(add)
        if first:
            # The first goal adopts the pre-goal coach log and chats, so nothing vanishes.
            def adopt(d: dict[str, Any]) -> None:
                for t in d.get("threads", {}).values():
                    if t["goal_id"] == NO_GOAL:
                        t["goal_id"] = goal["id"]
            self._threads_doc(user_id).update(adopt)
        self.coach_thread(user_id)  # pinned log for the new goal
        return goal

    def update_goal(self, user_id: str, goal_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        g = self._validate_goal(patch, partial=True)

        def apply(d: dict[str, Any]) -> dict[str, Any]:
            goal = d.get("goals", {}).get(goal_id)
            if goal is None:
                raise CoachError("goal not found")
            goal.update(g)
            return dict(goal)
        return self._goals_doc(user_id).update(apply)

    def set_plan(self, user_id: str, summary: str,
                 files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Store Rabbit's summary of the athlete's plan on the active goal, linking the
        uploaded originals (attachment metadata) for the UI."""
        g = self.active_goal(user_id)
        if g is None:
            raise CoachError("Save the goal first (save_goal), then the plan.")
        summary = _clean_text(summary, _GOAL_TEXT["plan"], "plan")

        def apply(d: dict[str, Any]) -> dict[str, Any]:
            goal = d["goals"][g["id"]]
            goal["plan"] = summary
            if files:
                known = {f["id"] for f in goal.get("plan_files", [])}
                goal["plan_files"] = (goal.get("plan_files", []) +
                                      [f for f in files if f["id"] not in known])[-MAX_PLAN_FILES:]
            return dict(goal)
        return self._goals_doc(user_id).update(apply)

    def archive_goal(self, user_id: str, goal_id: str) -> None:
        def apply(d: dict[str, Any]) -> None:
            goal = d.get("goals", {}).get(goal_id)
            if goal is None:
                raise CoachError("goal not found")
            goal["archived_at"] = goal.get("archived_at") or int(self._clock())
            if d.get("active") == goal_id:
                d["active"] = None
        self._goals_doc(user_id).update(apply)

    # ---------------------------------------------------- coach notes -----
    def notes(self, user_id: str) -> str:
        g = self.active_goal(user_id)
        if g is not None:
            return g.get("notes") or ""
        return self._s.user_doc(user_id, "profile").load().get("notes") or ""

    def set_notes(self, user_id: str, notes: str) -> None:
        notes = notes.strip()[:2000]
        g = self.active_goal(user_id)
        if g is not None:
            self.update_goal_notes(user_id, g["id"], notes)
        else:
            self._s.user_doc(user_id, "profile").update(lambda p: p.__setitem__("notes", notes))

    def update_goal_notes(self, user_id: str, goal_id: str, notes: str) -> None:
        def apply(d: dict[str, Any]) -> None:
            d["goals"][goal_id]["notes"] = notes
        self._goals_doc(user_id).update(apply)

    # --------------------------------------------------------- threads -----
    def _threads_doc(self, user_id: str):
        return self._s.user_doc(user_id, "threads")

    def _goal_key(self, user_id: str) -> str:
        g = self.active_goal(user_id)
        return g["id"] if g else NO_GOAL

    def coach_thread(self, user_id: str, goal_id: str | None = None) -> dict[str, Any]:
        goal_id = goal_id or self._goal_key(user_id)
        now = int(self._clock())

        def ensure(d: dict[str, Any]) -> dict[str, Any]:
            threads = d.setdefault("threads", {})
            for t in threads.values():
                if t["goal_id"] == goal_id and t["kind"] == "coach":
                    return dict(t)
            t = {"id": _new_id("th"), "goal_id": goal_id, "kind": "coach", "title": "Coach",
                 "created": now, "updated": now, "seed": None}
            threads[t["id"]] = t
            return dict(t)
        return self._threads_doc(user_id).update(ensure)

    def threads(self, user_id: str, goal_id: str | None = None) -> list[dict[str, Any]]:
        goal_id = goal_id or self._goal_key(user_id)
        coach = self.coach_thread(user_id, goal_id)
        all_t = self._threads_doc(user_id).load().get("threads", {}).values()
        topics = sorted((t for t in all_t if t["goal_id"] == goal_id and t["kind"] == "topic"),
                        key=lambda t: t["updated"], reverse=True)
        return [coach, *topics]

    def get_thread(self, user_id: str, thread_id: str) -> dict[str, Any] | None:
        if not isinstance(thread_id, str) or not re.fullmatch(r"th[0-9a-f]{12}", thread_id):
            return None
        t = self._threads_doc(user_id).load().get("threads", {}).get(thread_id)
        return dict(t) if t else None

    def create_thread(self, user_id: str, *, title: str | None = None,
                      seed: dict[str, str] | None = None) -> dict[str, Any]:
        """New topic thread under the active goal; optionally seeded from a coach
        message (``{"thread_id", "turn_id"}``). Adds a link card to the coach log."""
        seed_turn = None
        if seed is not None:
            src = self.get_thread(user_id, seed.get("thread_id", ""))
            seed_turn = self.get_turn(user_id, src["id"], seed.get("turn_id", "")) if src else None
            if seed_turn is None or not seed_turn.get("assistant"):
                raise CoachError("message not found")
        goal_id = self._goal_key(user_id)
        coach = self.coach_thread(user_id, goal_id)
        now = int(self._clock())
        t = {"id": _new_id("th"), "goal_id": goal_id, "kind": "topic",
             "title": _clean_text(title, 80, "title") if title else "",
             "created": now, "updated": now,
             "seed": {"thread_id": seed["thread_id"], "turn_id": seed["turn_id"]} if seed else None}

        def add(d: dict[str, Any]) -> None:
            threads = d.setdefault("threads", {})
            if len(threads) >= MAX_THREADS:
                raise CoachError("Too many threads; delete some old ones first.")
            threads[t["id"]] = t
        self._threads_doc(user_id).update(add)
        if seed_turn is not None:
            self.append_turn(user_id, t["id"], {
                "id": _new_id("t"), "ts": now, "kind": "seed", "label": seed_turn.get("label"),
                "user": None, "assistant": seed_turn["assistant"],
                "parts": [{"type": "text", "text": seed_turn["assistant"]}],
                "seed_from": {"thread_id": seed["thread_id"], "turn_id": seed["turn_id"]},
            }, touch=False)
        self.append_turn(user_id, coach["id"], {
            "id": _new_id("t"), "ts": now, "kind": "link", "user": None, "assistant": "",
            "parts": [{"type": "thread_link", "thread_id": t["id"],
                       "from_turn": seed["turn_id"] if seed else None}],
        }, touch=False)
        return dict(t)

    def rename_thread(self, user_id: str, thread_id: str, title: str) -> dict[str, Any]:
        title = _clean_text(title, 80, "title")

        def apply(d: dict[str, Any]) -> dict[str, Any]:
            t = d.get("threads", {}).get(thread_id)
            if t is None or t["kind"] != "topic":
                raise CoachError("thread not found")
            t["title"] = title
            return dict(t)
        return self._threads_doc(user_id).update(apply)

    def delete_thread(self, user_id: str, thread_id: str) -> None:
        def apply(d: dict[str, Any]) -> None:
            t = d.get("threads", {}).get(thread_id)
            if t is None or t["kind"] != "topic":
                raise CoachError("Only topic threads can be deleted.")
            del d["threads"][thread_id]
        self._threads_doc(user_id).update(apply)
        self._s.user_doc(user_id, f"conv-{thread_id}").delete()

    def clear_thread(self, user_id: str, thread_id: str) -> None:
        self._s.user_doc(user_id, f"conv-{thread_id}").delete()

    def _touch(self, user_id: str, thread_id: str) -> None:
        now = int(self._clock())

        def apply(d: dict[str, Any]) -> None:
            t = d.get("threads", {}).get(thread_id)
            if t is not None:
                t["updated"] = now
        self._threads_doc(user_id).update(apply)

    # ----------------------------------------------------------- turns -----
    def turns(self, user_id: str, thread_id: str) -> list[dict[str, Any]]:
        return self._s.user_doc(user_id, f"conv-{thread_id}").load().get("turns", [])

    def get_turn(self, user_id: str, thread_id: str, turn_id: str) -> dict[str, Any] | None:
        return next((t for t in self.turns(user_id, thread_id) if t.get("id") == turn_id), None)

    def append_turn(self, user_id: str, thread_id: str, turn: dict[str, Any], *,
                    touch: bool = True) -> None:
        def add(d: dict[str, Any]) -> None:
            turns = d.setdefault("turns", [])
            turns.append(turn)
            del turns[:-STORED_TURNS]
        self._s.user_doc(user_id, f"conv-{thread_id}").update(add)
        if touch:
            self._touch(user_id, thread_id)

    # --------------------------------------------------------- migrate -----
    def migrate_legacy(self, user_id: str) -> None:
        """Phase-3 single conversation -> coach thread (proactive) + one topic thread."""
        legacy = self._s.user_doc(user_id, "conversation")
        turns = legacy.load().get("turns", [])
        if not turns:
            return
        coach = self.coach_thread(user_id)
        chat = [t for t in turns if t.get("kind") != "proactive"]
        for t in turns:
            if t.get("kind") == "proactive":
                self.append_turn(user_id, coach["id"], t, touch=False)
        if chat:
            topic = self.create_thread(user_id, title="Earlier chat")
            for t in chat:
                self.append_turn(user_id, topic["id"], t, touch=False)
        legacy.delete()

    # --------------------------------------------------------- context -----
    def context_block(self, user_id: str, now: datetime, thread: dict[str, Any] | None) -> str:
        lines = [f"Today: {now:%A %Y-%m-%d}, {now:%H:%M} local time ({now.tzname()})."]
        a = {k: v for k, v in self.athlete(user_id).items() if v not in (None, "")}
        lines.append("Athlete profile: " + (
            "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in a.items()) if a
            else "not set up yet (ask about experience, weekly hours and injuries when it's useful)."))
        g = self.active_goal(user_id)
        if g:
            when = ""
            if g.get("date"):
                weeks, phase = training_phase(date.fromisoformat(g["date"]), now.date())
                when = (f" on {g['date']} ({weeks} weeks out, roughly the {phase} phase)"
                        if phase != "post-race" else f" on {g['date']} (already happened)")
            bits = [f"Active goal: {g.get('title')}{when}."]
            for k in ("event", "distance", "target", "priorities"):
                if g.get(k):
                    bits.append(f"{k.capitalize()}: {g[k]}.")
            lines.append(" ".join(bits))
            if g.get("plan"):
                plan = g["plan"]
                if len(plan) > PLAN_CONTEXT_CHARS:
                    plan = plan[:PLAN_CONTEXT_CHARS] + " ...[truncated]"
                lines.append(f"Their training plan (your summary of what they shared): {plan}")
            else:
                lines.append("Training plan: none shared yet. If it would help, invite them to "
                             "upload it (PDF or screenshots) or describe it.")
        else:
            lines.append("Active goal: none set yet. Onboarding: find out what they're training "
                         "for (race or event, date, target) and invite them to upload their plan. "
                         "Save what you learn as you go.")
        lines.append(f"Coach notes: {self.notes(user_id) or 'none yet'}")
        if thread is not None:
            lines.append("Thread: the coach log (briefs and workout notes)." if thread["kind"] == "coach"
                         else f"Thread: {thread.get('title') or 'new topic'}.")
        return "[Context]\n" + "\n".join(lines) + "\n[/Context]\n\n"
