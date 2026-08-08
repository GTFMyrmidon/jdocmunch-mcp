"""Unit tests for tool_surface counter mode in jdocmunch-mcp."""

import pytest
from jdocmunch_mcp import server


@pytest.mark.asyncio
async def test_tool_surface_counter_mode(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_TOOL_SURFACE", "counter")
    assert server._effective_surface() == "counter"

    tools = await server.list_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {"order", "menu", "route", "get_tool_details", "jdocmunch_guide"} or tool_names == {"order", "menu", "route", "get_tool_details"}

    stats = server._tool_surface_stats()
    assert stats["surface"] == "counter"
    assert stats["visible_tools"] == len(tools)
    assert stats["schema_tokens_avoided"] > 0


@pytest.mark.asyncio
async def test_tool_surface_full_mode(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_TOOL_SURFACE", "full")
    assert server._effective_surface() == "full"

    tools = await server.list_tools()
    assert len(tools) > 10
    assert "order" not in {t.name for t in tools}

    stats = server._tool_surface_stats()
    assert stats["surface"] == "full"
    assert stats["schema_tokens_avoided"] == 0
