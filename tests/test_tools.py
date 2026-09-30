from __future__ import annotations

import json
from typing import Any

import pytest

from garmin_mcp.providers import GarminError, ReauthRequired
from garmin_mcp.shaping import shape
from tests.conftest import make_service


def test_execute_read_and_cache(provider, registry) -> None:
    svc = make_service(provider, registry)
    out = svc.execute("andrew", "get_hrv_data", {"cdate": "2026-09-29"})
    assert out["result"]["hrvSummary"]["lastNightAvg"] == 62
    svc.execute("andrew", "get_hrv_data", {"cdate": "2026-09-29"})
    assert provider.sessions["andrew"].upstream_calls == 1  # second call cached


def test_execute_write_blocked_by_default(provider, registry) -> None:
    svc = make_service(provider, registry)
    with pytest.raises(GarminError, match="writes are disabled"):
        svc.execute("andrew", "delete_activity", {"activity_id": "1"})
    assert ("delete_activity", {"activity_id": "1"}) not in provider.garmin.calls
    assert provider.sessions["andrew"].upstream_calls == 0


def test_execute_write_allowed_when_enabled(provider, registry) -> None:
    svc = make_service(provider, registry, allow_writes=True)
    svc.execute("andrew", "add_weigh_in", {"weight": 70.5})
    assert ("add_weigh_in", {"weight": 70.5}) in provider.garmin.calls


def test_execute_blocked_methods_never_reach_client(provider, registry) -> None:
    svc = make_service(provider, registry, allow_writes=True)
    for name in ("login", "connectapi", "__init__", "get_sneaky"):
        with pytest.raises(GarminError):
            svc.execute("andrew", name, {})
    assert provider.sessions["andrew"].upstream_calls == 0


def test_unknown_user_gets_relogin_message(provider, registry) -> None:
    svc = make_service(provider, registry)
    with pytest.raises(ReauthRequired, match="garmin-mcp-login --user bob"):
        svc.execute("bob", "get_sleep_data", {"cdate": "2026-09-29"})


def test_bulky_fields_dropped_unless_full(provider, registry) -> None:
    svc = make_service(provider, registry, max_bytes=10_000_000)
    out = svc.execute("andrew", "get_activity_details", {"activity_id": "5"})
    assert "_omitted" in out["result"]["geoPolylineDTO"]
    assert "_omitted" in out["result"]["activityDetailMetrics"]
    assert "geoPolylineDTO" in out["omitted_fields"]
    full = svc.execute("andrew", "get_activity_details", {"activity_id": "5"}, full=True)
    assert len(full["result"]["activityDetailMetrics"]) == 3000


def test_hrv_readings_dropped(provider, registry) -> None:
    out = make_service(provider, registry).execute("andrew", "get_hrv_data", {"cdate": "2026-09-29"})
    assert "_omitted" in out["result"]["hrvReadings"]


def test_recent_activities_compact(provider, registry) -> None:
    out = make_service(provider, registry).recent_activities("andrew", days=7)
    assert out["count"] == 2
    swim, run = out["activities"]
    assert swim["pool_length"] == "25.0 meter" and swim["strokes"] == 1100
    assert swim["avg_pace_per_100m"] == "1:48"
    assert run["avg_pace_per_km"] == "5:00" and run["avg_power_w"] == 290
    assert run["distance_km"] == 10.0 and run["duration"] == "50:00"


def test_recent_activities_sport_filter(provider, registry) -> None:
    make_service(provider, registry).recent_activities("andrew", days=3, sport="bike")
    assert provider.garmin.calls[-1][1]["activitytype"] == "cycling"
    with pytest.raises(GarminError, match="Unknown sport"):
        make_service(provider, registry).recent_activities("andrew", sport="curling")


# ------------------------------------------------------------- truncation --
def test_truncation_trims_lists_and_says_so() -> None:
    data: dict[str, Any] = {"items": [{"name": f"activity {i}", "notes": "x" * 200} for i in range(500)]}
    out = shape(data, full=False, max_bytes=10_000)
    assert out["truncated"] is True
    assert "truncation_note" in out
    assert len(json.dumps(out)) < 12_000
    assert out["result"]["items"][-1]["_truncated"].endswith("more items omitted")


def test_truncation_falls_back_to_partial_json() -> None:
    data = {"blob": "y" * 50_000}
    out = shape(data, full=False, max_bytes=5_000)
    assert out["truncated"] is True and out["result"] is None
    assert len(out["partial_json"]) <= 5_000
    assert "Narrow the query" in out["truncation_note"]


def test_small_response_not_truncated() -> None:
    out = shape({"a": 1}, full=False, max_bytes=1000)
    assert out == {"truncated": False, "result": {"a": 1}}


def test_full_true_still_capped() -> None:
    out = shape({"xs": list(range(100_000))}, full=True, max_bytes=5_000)
    assert out["truncated"] is True
