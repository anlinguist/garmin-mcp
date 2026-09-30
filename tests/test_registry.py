from __future__ import annotations

import pytest

from garmin_mcp.registry import Access, ExecuteDenied, Registry


def test_index_built_from_real_library() -> None:
    reg = Registry()  # introspects the installed garminconnect
    assert len(reg) > 100
    assert reg.get("get_hrv_data").access is Access.READ
    assert reg.get("get_activity_splits").access is Access.READ
    assert reg.get("delete_activity").access is Access.WRITE
    for name in ("login", "resume_login", "logout", "connectapi", "connectwebproxy",
                 "download", "query_garmin_graphql", "upload_activity", "import_activity",
                 "download_activity"):
        assert reg.get(name).access is Access.BLOCKED, name
    assert all(not m.name.startswith("_") for m in reg.all())


def test_real_library_read_methods_have_no_write_verbs() -> None:
    reg = Registry()
    reads = [m for m in reg.all() if m.access is Access.READ]
    assert all(m.name.startswith(("get_", "count_")) for m in reads)


@pytest.mark.parametrize("query,expected", [
    ("hrv", "get_hrv_data"),
    ("sleep", "get_sleep_data"),
    ("activities by date", "get_activities_by_date"),
])
def test_search(registry: Registry, query: str, expected: str) -> None:
    names = [m.name for m in registry.search(query)]
    assert names and names[0] == expected


def test_search_real_library_tri_queries() -> None:
    reg = Registry()
    assert "get_training_readiness" in [m.name for m in reg.search("training readiness")]
    assert "get_hrv_data" in [m.name for m in reg.search("hrv")]
    assert any("power" in m.name for m in reg.search("bike power zones"))


def test_search_result_shape(registry: Registry) -> None:
    m = registry.search("hrv")[0].public(allow_writes=False)
    assert m["signature"].startswith("get_hrv_data(cdate: str)")
    assert m["access"] == "read" and m["callable"] is True
    assert m["summary"] == "Return HRV data for a date."


def test_classification_of_fakes(registry: Registry) -> None:
    assert registry.get("get_sneaky").access is Access.BLOCKED  # read name, write verb
    assert registry.get("get_file").access is Access.BLOCKED  # path param
    assert registry.get("get_kwargs").access is Access.BLOCKED  # variadic
    assert registry.get("frobnicate").access is Access.BLOCKED  # default deny
    assert registry.get("_private") is None


def test_writes_blocked_by_default(registry: Registry) -> None:
    with pytest.raises(ExecuteDenied, match="writes are disabled"):
        registry.resolve("add_weigh_in", {"weight": 70}, allow_writes=False)
    with pytest.raises(ExecuteDenied, match="writes are disabled"):
        registry.resolve("delete_activity", {"activity_id": "1"}, allow_writes=False)


def test_writes_allowed_when_enabled(registry: Registry) -> None:
    info, kwargs = registry.resolve("add_weigh_in", {"weight": 70}, allow_writes=True)
    assert info.name == "add_weigh_in" and kwargs == {"weight": 70}


@pytest.mark.parametrize("name", ["login", "connectapi", "get_sneaky", "get_file", "frobnicate"])
def test_blocked_even_with_writes(registry: Registry, name: str) -> None:
    with pytest.raises(ExecuteDenied):
        registry.resolve(name, {}, allow_writes=True)


@pytest.mark.parametrize("name", [
    "__init__", "__class__", "_private", "get_hrv_data.__globals__", "client.post",
    "__dict__", "", None, 123, ["get_hrv_data"], "get_hrv_data ", "unknown_method",
    "typed", "client",
])
def test_method_name_escape_attempts(registry: Registry, name: object) -> None:
    with pytest.raises(ExecuteDenied):
        registry.resolve(name, {}, allow_writes=True)


@pytest.mark.parametrize("args", [
    {"__class__": "x"},
    {"cdate": "2026-01-01", "self": 1},
    {"cdate": "2026-01-01", "extra": 1},
    {"_cdate": "x"},
    {"cdate": object()},
    {"cdate": b"bytes"},
    ["2026-01-01"],
    "cdate=2026-01-01",
    {"cdate": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}},
    {"cdate": "x" * 30_000},
])
def test_argument_smuggling_rejected(registry: Registry, args: object) -> None:
    with pytest.raises(ExecuteDenied):
        registry.resolve("get_hrv_data", args, allow_writes=True)


def test_missing_required_arg(registry: Registry) -> None:
    with pytest.raises(ExecuteDenied, match="bad arguments"):
        registry.resolve("get_hrv_data", {}, allow_writes=False)


def test_dispatch_uses_registered_function_not_getattr(registry: Registry) -> None:
    info, _ = registry.resolve("get_hrv_data", {"cdate": "2026-09-29"}, allow_writes=False)
    from tests.conftest import FakeGarmin

    assert info.fn is FakeGarmin.__dict__["get_hrv_data"]
