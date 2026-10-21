from __future__ import annotations

from typing import Any

import pytest

from garmin_mcp import social
from garmin_mcp.providers import GarminError
from garmin_mcp.registry import Access, Registry
from tests.conftest import FakeGarmin, FakeProvider, FakeSession, make_service

CONNS = [
    {"userId": 11, "displayName": "sam-dn", "fullName": "Sam Lee", "location": "Boise",
     "profileImageUrls": {"PROFILE": "https://img"}},
    {"userId": 12, "displayName": "ortiz-dn", "fullName": "Sam Ortiz"},
    {"userId": 13, "displayName": "jordan-dn", "fullName": "Jordan Reyes"},
    {"userId": 14, "displayName": "../../admin", "fullName": "Mallory"},
]


class FakeSocialGarmin(FakeGarmin):
    """FakeGarmin whose connectapi serves canned connection payloads by path."""

    def __init__(self, display_name: str = "me-dn", conns: list[dict[str, Any]] = CONNS) -> None:
        super().__init__()
        self.display_name = display_name
        self.conns = conns
        self.requests: list[tuple[str, dict[str, str] | None]] = []

    def connectapi(self, path: str, **kwargs: Any) -> Any:
        params = kwargs.get("params")
        self.requests.append((path, params))
        if path.startswith("/connection-service/connection/v2/connections/pagination/"):
            start, limit = int(params["start"]), int(params["limit"])
            return {"userConnections": self.conns[start - 1:start - 1 + limit]}
        if path.startswith("/activitylist-service/activities/"):
            return {"activityList": [
                {"activityId": 501, "activityName": "Long run", "startTimeLocal": "2026-10-19 07:00:00",
                 "activityType": {"typeKey": "running"}, "distance": 20000.0, "duration": 6000.0,
                 "averageSpeed": 3.33, "averageHR": 150, "ownerId": 13,
                 "privacy": {"typeId": 3, "typeKey": "subscribers"}},
            ], "requestorRelationship": "CONNECTED"}
        if path.startswith("/userprofile-service/socialProfile/"):
            return {"fullName": "Jordan Reyes", "activityHeartRateVisibility": "public",
                    "profileImageUrlLarge": "https://img", "garminGUID": "guid", "bio": None}
        if path.startswith("/userstats-service/statistics/"):
            return {"userProfileId": 13, "userMetrics": [
                {"userProfileId": 13, "activityType": {"typeKey": "running"},
                 "parentActivityType": None, "totalActivities": 4, "totalDistance": 42000.0}]}
        if path.startswith("/usersummary-service/stats/connectLifetimeTotals/"):
            return {"userProfileId": 13, "totalSteps": 1_000_000}
        if path.startswith("/personalrecord-service/personalrecord/prs/"):
            return [{"typeId": 3, "value": 1290.0, "activityId": 77, "activityName": "Park 5k",
                     "activityType": "running", "actStartDateTimeInGMTFormatted": "2026-05-01T14:00:00.0"}]
        if path.startswith("/personalrecord-service/personalrecordtype/prtypes/"):
            return [{"id": 3, "key": "pr.label.5k.run"}]
        if path == "/usersummary-service/stats/wellness/leaderboard/FOLLOWING":
            return {"userProfileId": 99, "userRank": 2, "wellnessLeaderBoardMetricType": "TOTAL_STEPS",
                    "userStats": [
                        {"rank": 1, "userProfilePk": 13, "totalValue": 90000.0, "allPrivate": False},
                        {"rank": 2, "userProfilePk": 99, "totalValue": 80000.0, "allPrivate": False},
                        {"rank": 3, "userProfilePk": 12, "totalValue": 0.0, "allPrivate": True},
                    ]}
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def social_registry() -> Registry:
    return Registry(FakeGarmin, extensions=social.METHODS)


@pytest.fixture
def social_provider() -> FakeProvider:
    p = FakeProvider()
    p.garmin = FakeSocialGarmin()
    p.sessions = {
        "andrew": FakeSession("andrew", p.garmin),
        "bea": FakeSession("bea", FakeSocialGarmin("bea-dn", [CONNS[0]])),
    }
    return p


def paths(garmin: FakeSocialGarmin) -> list[str]:
    return [p for p, _ in garmin.requests]


# --------------------------------------------------------------- registry --
def test_extensions_register_as_read_on_real_library() -> None:
    reg = Registry(extensions=social.METHODS)
    for fn in social.METHODS:
        info = reg.get(fn.__name__)
        assert info.access is Access.READ, fn.__name__
        assert info.resolves_connection is ("connection" in info.params)
    assert reg.get("get_connection_activities").resolves_connection
    assert not reg.get("get_connections").resolves_connection
    assert reg.get("connectapi").access is Access.BLOCKED


def test_extensions_off_by_default() -> None:
    reg = Registry(FakeGarmin)
    assert reg.get("get_connections") is None
    assert reg.get("get_connection_activities") is None


def test_extension_name_collision_fails_startup() -> None:
    class Library:
        def get_connections(self) -> Any:
            """Upstream added the same name."""

    with pytest.raises(RuntimeError, match="collides"):
        Registry(Library, extensions=social.METHODS)


def test_non_read_extensions_fail_startup() -> None:
    def unclassified_ext(client: Any) -> Any:
        return 1

    def get_sneaky_ext(client: Any) -> Any:
        return client.post("connectapi", "/x")

    for fn in (unclassified_ext, get_sneaky_ext):
        with pytest.raises(RuntimeError, match="not read-only"):
            Registry(FakeGarmin, extensions=(fn,))


def test_search_finds_friend_methods(social_registry: Registry) -> None:
    names = [m.name for m in social_registry.search("friends activities")]
    assert names[0] == "get_connection_activities"
    assert "get_connections" in [m.name for m in social_registry.search("friends")]


# ------------------------------------------------------------- resolution --
@pytest.mark.parametrize("wanted", ["jordan", "Jordan Reyes", "  JORDAN   reyes ", "reyes", "jordan-dn"])
def test_connection_resolves_by_name(social_provider, social_registry, wanted) -> None:
    svc = make_service(social_provider, social_registry)
    out = svc.execute("andrew", "get_connection_profile", {"connection": wanted})
    assert out["connection"] == {"display_name": "jordan-dn", "full_name": "Jordan Reyes"}
    assert "/userprofile-service/socialProfile/jordan-dn" in paths(social_provider.garmin)


def test_ambiguous_connection_lists_candidates(social_provider, social_registry) -> None:
    svc = make_service(social_provider, social_registry)
    with pytest.raises(GarminError, match="Sam Lee, Sam Ortiz"):
        svc.execute("andrew", "get_connection_activities", {"connection": "sam"})
    assert not any("activitylist" in p for p in paths(social_provider.garmin))


@pytest.mark.parametrize("wanted", ["zed", "", "   "])
def test_unknown_connection_never_reaches_friend_endpoint(social_provider, social_registry,
                                                          wanted) -> None:
    svc = make_service(social_provider, social_registry)
    with pytest.raises(GarminError):
        svc.execute("andrew", "get_connection_stats", {"connection": wanted})
    assert not any("userstats" in p for p in paths(social_provider.garmin))


def test_non_connection_display_name_is_rejected(social_provider, social_registry) -> None:
    """A display name that isn't in the caller's list can't be queried directly."""
    svc = make_service(social_provider, social_registry)
    with pytest.raises(GarminError, match="No connection matches"):
        svc.execute("andrew", "get_connection_profile", {"connection": "stranger-dn"})


def test_hostile_display_name_never_builds_a_path(social_provider, social_registry) -> None:
    svc = make_service(social_provider, social_registry)
    with pytest.raises(ValueError, match="unsupported format"):  # GarminError via translate_error
        svc.execute("andrew", "get_connection_profile", {"connection": "Mallory"})
    assert not any("admin" in p for p in paths(social_provider.garmin))


@pytest.mark.parametrize("dn", ["../x", "..", ".", "a/b", "a%2Fb", "a?b", "a b", "", None, 5,
                                "x" * 101])
def test_display_name_validation(dn) -> None:
    with pytest.raises(ValueError):
        social._display_name(dn)


def test_display_name_accepts_garmin_formats() -> None:
    assert social._display_name("0f3c9a52-7d1e-4b8a-9c26-5e4d8a1b7f90").count("-") == 4
    assert social._display_name("john.smith_99") == "john.smith_99"


def test_resolution_is_per_user(social_provider, social_registry) -> None:
    svc = make_service(social_provider, social_registry)
    svc.execute("andrew", "get_connections", {})  # andrew's list (with Jordan) is cached
    with pytest.raises(GarminError, match="No connection matches"):
        svc.execute("bea", "get_connection_profile", {"connection": "jordan"})
    bea = social_provider.sessions["bea"].garmin
    assert paths(bea) == ["/connection-service/connection/v2/connections/pagination/bea-dn"]


def test_connection_list_and_results_are_cached(social_provider, social_registry) -> None:
    svc = make_service(social_provider, social_registry)
    for _ in range(2):
        svc.execute("andrew", "get_connection_activities", {"connection": "jordan"})
    assert social_provider.sessions["andrew"].upstream_calls == 2  # list + activities, once each


# ---------------------------------------------------------------- methods --
def test_get_connections_compact_and_paged() -> None:
    many = [{"userId": i, "displayName": f"dn{i}", "fullName": f"Person {i}"} for i in range(150)]
    g = FakeSocialGarmin(conns=many)
    out = social.get_connections(g)
    assert out["count"] == 150
    assert out["connections"][0] == {"display_name": "dn0", "full_name": "Person 0", "user_id": 0}
    assert [p["start"] for _, p in g.requests] == ["1", "101"]
    assert all(p.endswith("/me-dn") for p in paths(g))


def test_get_connections_drops_images() -> None:
    out = social.get_connections(FakeSocialGarmin())
    assert "profileImageUrls" not in str(out)
    assert out["connections"][0]["location"] == "Boise"


def test_activities_are_one_based_and_compact() -> None:
    g = FakeSocialGarmin()
    out = social.get_connection_activities(g, "jordan-dn", start=0, limit=5)
    assert g.requests[-1][1] == {"start": "1", "limit": "5"}
    act = out["activities"][0]
    assert act["activity_id"] == 501 and act["sport"] == "running"
    assert act["avg_pace_per_km"] and act["privacy"] == "subscribers"
    assert "ownerId" not in act


@pytest.mark.parametrize("kwargs", [{"limit": 101}, {"limit": 0}, {"start": -1}, {"limit": True},
                                    {"start": "0"}])
def test_activities_bounds(kwargs) -> None:
    with pytest.raises(ValueError):
        social.get_connection_activities(FakeSocialGarmin(), "jordan-dn", **kwargs)


def test_profile_is_trimmed() -> None:
    out = social.get_connection_profile(FakeSocialGarmin(), "jordan-dn")
    assert out == {"fullName": "Jordan Reyes", "activityHeartRateVisibility": "public"}


def test_stats_periods() -> None:
    g = FakeSocialGarmin()
    out = social.get_connection_stats(g, "jordan-dn")
    assert out["lifetime"] == {"totalSteps": 1_000_000}
    assert out["metrics"] == [{"activityType": "running", "parentActivityType": None,
                               "totalActivities": 4, "totalDistance": 42000.0}]
    social.get_connection_stats(g, "jordan-dn", period="monthly")
    assert g.requests[-1] == ("/userstats-service/statistics/monthly/jordan-dn", {"ByParentType": "true"})
    with pytest.raises(ValueError, match="period"):
        social.get_connection_stats(g, "jordan-dn", period="weekly")


def test_personal_records_named() -> None:
    out = social.get_connection_personal_records(FakeSocialGarmin(), "jordan-dn")
    assert out["records"][0]["type"] == "5k.run"
    assert out["records"][0]["activity_id"] == 77


def test_leaderboard_names_and_privacy() -> None:
    out = social.get_connections_leaderboard(FakeSocialGarmin(), "2026-10-19", "2026-10-25")
    assert out["entries"] == [
        {"rank": 1, "name": "Jordan Reyes", "total": 90000.0},
        {"rank": 2, "name": "you", "total": 80000.0},
        {"rank": 3, "name": "Sam Ortiz", "private": True},
    ]
    assert out["your_rank"] == 2


@pytest.mark.parametrize("start,end", [("2026-10-25", "2026-10-19"), ("2026-09-01", "2026-10-25"),
                                       ("2026-10-19", "soon")])
def test_leaderboard_dates(start, end) -> None:
    with pytest.raises(ValueError):
        social.get_connections_leaderboard(FakeSocialGarmin(), start, end)
