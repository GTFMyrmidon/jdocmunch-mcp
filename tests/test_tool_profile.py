"""Tests for JDOCMUNCH_TOOL_PROFILE / JDOCMUNCH_DISABLED_TOOLS filtering (issue #297)."""

import json
import os
import asyncio

import pytest

from jdocmunch_mcp.server import (
    _all_tools,
    _filter_tools,
    _TOOL_TIER_CORE,
    _TOOL_TIER_STANDARD,
    _ALWAYS_PRESENT_TOOLS,
    call_tool,
    list_tools,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("JDOCMUNCH_TOOL_PROFILE", raising=False)
    monkeypatch.delenv("JDOCMUNCH_DISABLED_TOOLS", raising=False)
    yield


def test_full_default_returns_every_tool():
    """No env vars → all tools surfaced."""
    tools = _filter_tools(_all_tools())
    assert len(tools) == len(_all_tools())


def test_core_profile_drops_to_core_tier_plus_always_present(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "core")
    tools = _filter_tools(_all_tools())
    names = {t.name for t in tools}
    assert names <= _TOOL_TIER_CORE | _ALWAYS_PRESENT_TOOLS
    assert "jdocmunch_guide" in names
    # Sanity: a non-core tool is hidden.
    assert "analyze_perf" not in names


def test_standard_profile_is_superset_of_core(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "standard")
    tools = _filter_tools(_all_tools())
    names = {t.name for t in tools}
    assert _TOOL_TIER_CORE <= names


def test_invalid_profile_falls_back_to_full(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "ultra")
    tools = _filter_tools(_all_tools())
    assert len(tools) == len(_all_tools())


def test_disabled_tools_filters_out_named_tools(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_DISABLED_TOOLS", "analyze_perf, tune_weights")
    tools = _filter_tools(_all_tools())
    names = {t.name for t in tools}
    assert "analyze_perf" not in names
    assert "tune_weights" not in names
    assert "search_sections" in names


def test_disabled_tools_can_hide_jdocmunch_guide(monkeypatch):
    """Issue #297/#298: jdocmunch_guide is documentation, not a control surface."""
    monkeypatch.setenv("JDOCMUNCH_DISABLED_TOOLS", "jdocmunch_guide")
    tools = _filter_tools(_all_tools())
    assert "jdocmunch_guide" not in {t.name for t in tools}


def test_core_profile_plus_disabled_compose(monkeypatch):
    """core tier AND disabled_tools compose: drop both non-core and explicit removes."""
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "core")
    monkeypatch.setenv("JDOCMUNCH_DISABLED_TOOLS", "search_titles")
    tools = _filter_tools(_all_tools())
    names = {t.name for t in tools}
    assert "search_titles" not in names
    assert "search_sections" in names  # still in core


def test_call_tool_rejects_disabled_tool(monkeypatch):
    """Even if a client cached the schema, calling a disabled tool gets a clear error."""
    monkeypatch.setenv("JDOCMUNCH_DISABLED_TOOLS", "analyze_perf")
    out = asyncio.run(call_tool("analyze_perf", {}))
    payload = json.loads(out[0].text)
    assert "disabled" in payload["error"].lower()


def test_list_tools_async_entrypoint_applies_filter(monkeypatch):
    """The @server.list_tools()-decorated coroutine honors env config."""
    monkeypatch.setenv("JDOCMUNCH_TOOL_PROFILE", "core")
    tools = asyncio.run(list_tools())
    assert all(t.name in (_TOOL_TIER_CORE | _ALWAYS_PRESENT_TOOLS) for t in tools)
