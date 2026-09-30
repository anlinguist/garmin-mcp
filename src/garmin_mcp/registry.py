"""Method index built by introspecting the installed garminconnect ``Garmin`` class.

Security model for ``execute``:
  * Dispatch is by exact dictionary lookup into this registry. The stored value
    is the unbound function object taken from ``Garmin.__dict__`` at startup;
    nothing is resolved with getattr on user input at call time, so there's
    no attribute traversal, dunder access, or bound-method smuggling.
  * Classification is default-deny: a method is READ only if its name starts
    with a read prefix AND its source contains no write HTTP verbs; WRITE only
    by explicit write prefix; everything else is BLOCKED.
  * Methods that take filesystem paths, return raw bytes, accept free-form
    request bodies, or touch credentials are always BLOCKED.
  * Arguments must bind to the real signature (no *args/**kwargs), keys must be
    plain identifiers, and values must be JSON scalars/containers.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

READ_PREFIXES = ("get_", "count_")
WRITE_PREFIXES = (
    "add_", "set_", "delete_", "upload_", "create_", "update_", "remove_",
    "schedule_", "unschedule_", "push_", "import_", "init_", "confirm_",
    "request_reload",
)
# Never callable, regardless of GARMIN_ALLOW_WRITES.
ALWAYS_BLOCKED = {
    "login", "resume_login", "logout",           # credentials
    "connectapi", "connectwebproxy", "download",  # raw path passthrough
    "query_garmin_graphql",                       # free-form POST body
    "upload_activity", "import_activity",         # read local file paths
    "typed",
}
_WRITE_VERB_RE = re.compile(
    r"\.(post|put|delete|patch)\(|method\s*=\s*[\"'](POST|PUT|DELETE|PATCH)", re.I
)
_PATHLIKE_PARAM_RE = re.compile(r"(path|file|filename|dir)$", re.I)
_ARG_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
_MAX_ARG_DEPTH = 6
_MAX_ARG_STR = 20_000


class Access(str, Enum):
    READ = "read"
    WRITE = "write"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class MethodInfo:
    name: str
    signature: str
    summary: str
    access: Access
    params: tuple[str, ...]
    fn: Any
    reason: str = ""

    def public(self, allow_writes: bool) -> dict[str, Any]:
        return {
            "name": self.name,
            "signature": f"{self.name}{self.signature}",
            "summary": self.summary,
            "access": self.access.value,
            "callable": self.callable(allow_writes),
            **({"note": self.reason} if self.reason else {}),
        }

    def callable(self, allow_writes: bool) -> bool:
        return self.access is Access.READ or (self.access is Access.WRITE and allow_writes)


class ExecuteDenied(Exception):
    pass


def _summary(fn: Any) -> str:
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        line = line.strip()
        if line and not line.startswith(":"):
            return line[:200]
    return ""


def _classify(name: str, fn: Any, sig: inspect.Signature) -> tuple[Access, str]:
    if name in ALWAYS_BLOCKED:
        return Access.BLOCKED, "credential/raw-request/file method"
    if name.startswith("download_"):
        return Access.BLOCKED, "returns binary data"
    for p in list(sig.parameters.values())[1:]:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            return Access.BLOCKED, "variadic signature"
        if _PATHLIKE_PARAM_RE.search(p.name):
            return Access.BLOCKED, "takes a filesystem path"
    ret = sig.return_annotation
    if ret in (bytes, "bytes"):
        return Access.BLOCKED, "returns binary data"
    if name.startswith(READ_PREFIXES):
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            return Access.BLOCKED, "cannot verify source is read-only"
        if _WRITE_VERB_RE.search(src):
            return Access.BLOCKED, "read-named method issues a write verb"
        return Access.READ, ""
    if name.startswith(WRITE_PREFIXES):
        return Access.WRITE, ""
    return Access.BLOCKED, "unclassified method (default deny)"


class Registry:
    def __init__(self, cls: type | None = None) -> None:
        if cls is None:
            from garminconnect import Garmin

            cls = Garmin
        self._methods: dict[str, MethodInfo] = {}
        for name, fn in vars(cls).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            sig = inspect.signature(fn)
            access, reason = _classify(name, fn, sig)
            params = tuple(list(sig.parameters)[1:])
            shown = sig.replace(parameters=list(sig.parameters.values())[1:])
            self._methods[name] = MethodInfo(
                name=name,
                signature=re.sub(r"'([^']*)'", r"\1", str(shown)).replace("typing.", ""),
                summary=_summary(fn),
                access=access,
                params=params,
                fn=fn,
                reason=reason,
            )
        self._tokens = {
            n: set(_tokenize(f"{n} {m.summary} {' '.join(m.params)}"))
            for n, m in self._methods.items()
        }

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._methods

    def __len__(self) -> int:
        return len(self._methods)

    def all(self) -> list[MethodInfo]:
        return list(self._methods.values())

    def get(self, name: str) -> MethodInfo | None:
        if not isinstance(name, str):
            return None
        return self._methods.get(name)

    # ---------------------------------------------------------------- search --
    def search(self, query: str, limit: int = 10) -> list[MethodInfo]:
        q = set(_tokenize(query))
        for t in list(q):
            q |= _SYNONYMS.get(t, set())
        if not q:
            return []
        scored: list[tuple[float, str]] = []
        for name, toks in self._tokens.items():
            name_toks = set(_tokenize(name))
            score = 3 * len(q & name_toks) + len(q & toks)
            if score:
                info = self._methods[name]
                # Prefer readable methods; demote blocked ones.
                score += {Access.READ: 0.5, Access.WRITE: 0, Access.BLOCKED: -1}[info.access]
                scored.append((score, name))
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self._methods[n] for _, n in scored[: max(1, min(limit, 50))]]

    # ------------------------------------------------------- execute guards --
    def resolve(self, name: Any, args: Any, allow_writes: bool) -> tuple[MethodInfo, dict[str, Any]]:
        """Validate a method name + args. Returns the MethodInfo and bound kwargs."""
        if not isinstance(name, str) or not _ARG_NAME_RE.match(name):
            raise ExecuteDenied("method must be a plain method name")
        info = self._methods.get(name)
        if info is None:
            raise ExecuteDenied(f"unknown method {name!r}; use the search tool")
        if info.access is Access.BLOCKED:
            raise ExecuteDenied(f"{name} is blocked ({info.reason})")
        if info.access is Access.WRITE and not allow_writes:
            raise ExecuteDenied(
                f"{name} is a write method; writes are disabled (GARMIN_ALLOW_WRITES=false)"
            )
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ExecuteDenied("args must be a JSON object")
        for key, value in args.items():
            if not isinstance(key, str) or not _ARG_NAME_RE.match(key):
                raise ExecuteDenied(f"invalid argument name {key!r}")
            if key not in info.params:
                raise ExecuteDenied(f"{name} has no parameter {key!r}; expected {list(info.params)}")
            _check_json_value(value, 0)
        sig = inspect.signature(info.fn)
        try:
            sig.bind(object(), **args)
        except TypeError as e:
            raise ExecuteDenied(f"bad arguments for {name}: {e}") from None
        return info, dict(args)


def _check_json_value(value: Any, depth: int) -> None:
    if depth > _MAX_ARG_DEPTH:
        raise ExecuteDenied("argument nesting too deep")
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if len(value) > _MAX_ARG_STR:
            raise ExecuteDenied("argument string too long")
        return
    if isinstance(value, list):
        for v in value:
            _check_json_value(v, depth + 1)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ExecuteDenied("object keys must be strings")
            _check_json_value(v, depth + 1)
        return
    raise ExecuteDenied(f"unsupported argument type {type(value).__name__}")


def _tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOP]


_STOP = {"get", "the", "a", "an", "of", "for", "and", "to", "my", "me", "return", "returns", "data"}
_SYNONYMS: dict[str, set[str]] = {
    "run": {"running", "activities", "activity"},
    "running": {"activities", "activity"},
    "bike": {"cycling", "activities", "activity", "power", "ftp"},
    "ride": {"cycling", "activities", "activity"},
    "cycling": {"activities", "power", "ftp"},
    "swim": {"swimming", "activities", "activity"},
    "brick": {"multi", "sport", "activities"},
    "workout": {"activities", "activity", "workouts"},
    "workouts": {"workout", "activities"},
    "hr": {"heart", "rate", "rates", "rhr"},
    "heart": {"rate", "rates", "rhr"},
    "pace": {"activity", "splits"},
    "splits": {"split", "laps"},
    "laps": {"splits", "split"},
    "vo2": {"max", "metrics"},
    "vo2max": {"max", "metrics"},
    "load": {"training", "status", "balance"},
    "readiness": {"training", "morning"},
    "recovery": {"training", "readiness", "hrv", "body", "battery"},
    "fitness": {"training", "status", "age", "max", "metrics", "endurance"},
    "zones": {"zone", "timezones"},
    "weight": {"weigh", "body", "composition"},
    "tss": {"training", "load"},
}
