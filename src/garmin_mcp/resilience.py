"""TTL cache and per-user rate limiting (backoff lives in the provider session)."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 512,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(user_id: str, method: str, args: dict[str, Any]) -> str:
        return json.dumps([user_id, method, args], sort_keys=True, default=str)

    def get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return False, None
            expires, value = item
            if self._clock() >= expires:
                del self._data[key]
                return False, None
            self._data.move_to_end(key)
            return True, value

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            self._data[key] = (self._clock() + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear_user(self, user_id: str) -> None:
        prefix = json.dumps([user_id])[:-1]
        with self._lock:
            for k in [k for k in self._data if k.startswith(prefix)]:
                del self._data[k]


class RateLimiter:
    """Token bucket per user: ``per_minute`` sustained, burst of the same size."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = per_minute / 60.0
        self._cap = float(per_minute)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, user_id: str) -> bool:
        if self._cap <= 0:
            return True
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(user_id, (self._cap, now))
            tokens = min(self._cap, tokens + (now - last) * self._rate)
            if tokens < 1:
                self._buckets[user_id] = (tokens, now)
                return False
            self._buckets[user_id] = (tokens - 1, now)
            return True
