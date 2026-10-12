"""The MCP server's tool docstrings and Rabbit's agent tool descriptions must not drift."""

from __future__ import annotations

import inspect
import re

from garmin_mcp import server
from garmin_mcp.toolspec import RECENT_DESC, SEARCH_DESC


def _docstring(name: str) -> str:
    src = inspect.getsource(server.build_app)
    m = re.search(rf'def {name}\(.*?\).*?:\n\s+"""(.*?)"""', src, re.S)
    assert m, name
    return " ".join(m.group(1).split())


def test_mcp_and_agent_tool_descriptions_match() -> None:
    assert _docstring("search") == SEARCH_DESC
    assert _docstring("recent_activities") == RECENT_DESC
