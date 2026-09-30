from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from garmin_mcp.providers import GarminProvider, GarminSession, ReauthRequired
from garmin_mcp.registry import Registry
from garmin_mcp.resilience import RateLimiter, TTLCache
from garmin_mcp.tools import GarminService


class FakeGarmin:
    """Stand-in for garminconnect.Garmin with a few representative methods."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_hrv_data(self, cdate: str) -> dict[str, Any]:
        """Return HRV data for a date."""
        self.calls.append(("get_hrv_data", {"cdate": cdate}))
        return {"hrvSummary": {"lastNightAvg": 62}, "hrvReadings": list(range(300))}

    def get_sleep_data(self, cdate: str) -> dict[str, Any]:
        """Return sleep data."""
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000}}

    def get_activities_by_date(self, startdate: str, enddate: str | None = None,
                               activitytype: str | None = None,
                               sortorder: str | None = None) -> list[dict[str, Any]]:
        """Fetch available activities between specific dates."""
        self.calls.append(("get_activities_by_date", {"startdate": startdate,
                                                      "activitytype": activitytype}))
        return [
            {"activityId": 1, "activityName": "Pool swim", "startTimeLocal": "2026-09-28 06:00:00",
             "activityType": {"typeKey": "lap_swimming"}, "duration": 2700.0,
             "distance": 2500.0, "averageSpeed": 0.93, "averageHR": 140, "maxHR": 162,
             "poolLength": 25.0, "unitOfPoolLength": {"unitKey": "meter"}, "strokes": 1100,
             "aerobicTrainingEffect": 3.1},
            {"activityId": 2, "activityName": "Tempo run", "startTimeLocal": "2026-09-29 07:00:00",
             "activityType": {"typeKey": "running"}, "duration": 3000.0,
             "distance": 10000.0, "averageSpeed": 3.333, "averageHR": 155, "maxHR": 171,
             "avgPower": 290},
        ]

    def get_activity_details(self, activity_id: str, maxchart: int = 2000,
                             maxpoly: int = 4000) -> dict[str, Any]:
        """Return activity details."""
        return {"activityId": activity_id, "geoPolylineDTO": {"polyline": [[1, 2]] * 500},
                "activityDetailMetrics": [{"metrics": [1.0, 2.0]}] * 3000}

    def add_weigh_in(self, weight: float, unitKey: str = "kg") -> dict[str, Any]:
        """Add a weigh-in."""
        self.calls.append(("add_weigh_in", {"weight": weight}))
        return {"ok": True}

    def delete_activity(self, activity_id: str) -> Any:
        """Delete activity."""
        self.calls.append(("delete_activity", {"activity_id": activity_id}))
        return None

    def login(self, tokenstore: str | None = None) -> Any:
        """Log in."""
        raise AssertionError("must never be callable")

    def connectapi(self, path: str, **kwargs: Any) -> Any:
        """Raw API call."""
        raise AssertionError("must never be callable")

    def get_sneaky(self, cdate: str) -> Any:
        """Read-named but writes."""
        return self.client.post("connectapi", "/x")  # type: ignore[attr-defined]

    def get_file(self, path: str) -> Any:
        """Takes a path."""
        return open(path).read()

    def get_kwargs(self, **kwargs: Any) -> Any:
        """Variadic."""
        return kwargs

    def frobnicate(self) -> Any:
        """Unknown prefix."""
        return 1

    def _private(self) -> Any:
        return 1


class FakeSession(GarminSession):
    def __init__(self, user_id: str, garmin: FakeGarmin) -> None:
        self.user_id = user_id
        self.garmin = garmin
        self.upstream_calls = 0

    def call(self, fn: Any, kwargs: dict[str, Any], *, retry: bool) -> Any:
        self.upstream_calls += 1
        return fn(self.garmin, **kwargs)


class FakeProvider(GarminProvider):
    def __init__(self) -> None:
        self.garmin = FakeGarmin()
        self.sessions: dict[str, FakeSession] = {"andrew": FakeSession("andrew", self.garmin)}

    def get_client(self, user_id: str) -> FakeSession:
        if user_id not in self.sessions:
            raise ReauthRequired(user_id, "no Garmin tokens stored")
        return self.sessions[user_id]

    def login_start(self, user_id, email, password):  # pragma: no cover
        raise NotImplementedError

    def login_complete(self, handle, mfa_code):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def registry() -> Registry:
    return Registry(FakeGarmin)


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


def make_service(provider: FakeProvider, registry: Registry, *, allow_writes: bool = False,
                 max_bytes: int = 60_000) -> GarminService:
    return GarminService(
        provider, registry, allow_writes=allow_writes, cache=TTLCache(300),
        limiter=RateLimiter(100), max_response_bytes=max_bytes,
    )
