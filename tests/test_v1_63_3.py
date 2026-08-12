"""jdocmunch_guide tool (v1.63.3) — sibling-parity with jcm's jcodemunch_guide."""

from __future__ import annotations

import asyncio
import json

import pytest

from jdocmunch_mcp.server import call_tool, list_tools


class TestJdocmunchGuide:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "jdocmunch_guide" in names

    @pytest.mark.asyncio
    async def test_empty_input_schema(self):
        tools = await list_tools()
        guide = next(t for t in tools if t.name == "jdocmunch_guide")
        assert guide.inputSchema["type"] == "object"
        # No required fields.
        assert "required" not in guide.inputSchema or not guide.inputSchema["required"]

    @pytest.mark.asyncio
    async def test_returns_version_and_content(self):
        result = await call_tool("jdocmunch_guide", {})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert "version" in payload
        assert "content" in payload
        assert isinstance(payload["content"], str)
        assert len(payload["content"]) > 0

    @pytest.mark.asyncio
    async def test_content_mentions_quickstart_tools(self):
        result = await call_tool("jdocmunch_guide", {})
        content = json.loads(result[0].text)["content"]
        # Quick-start path names.
        for tool in ("doc_list_repos", "index_local", "search_sections",
                     "get_section", "get_toc_tree"):
            assert tool in content, f"quick-start tool {tool} missing from guide"

    @pytest.mark.asyncio
    async def test_content_includes_self_reference(self):
        # The Self-Guide category should mention the tool itself so an agent
        # can see "I'm allowed to call this" in the surface inventory.
        result = await call_tool("jdocmunch_guide", {})
        content = json.loads(result[0].text)["content"]
        assert "jdocmunch_guide" in content

    @pytest.mark.asyncio
    async def test_idempotent(self):
        a = await call_tool("jdocmunch_guide", {})
        b = await call_tool("jdocmunch_guide", {})
        assert a[0].text == b[0].text
