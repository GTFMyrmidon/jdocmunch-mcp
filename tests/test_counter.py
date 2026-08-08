"""Unit tests for jdocmunch-mcp counter module."""

import json
import pytest

from jdocmunch_mcp import counter
from jdocmunch_mcp import server


def test_order_gate():
    catalog = server._catalog_names()

    # Empty action
    assert "non-empty" in counter.order_gate("", catalog, False)

    # Front door recursion
    assert "front-door tool" in counter.order_gate("order", catalog, False)

    # Unknown action
    assert "Unknown action" in counter.order_gate("invalid_tool_name", catalog, False)

    # Forbidden verb (tripwire)
    assert "read-only dispatch surface" in counter.order_gate("exec_shell", catalog | {"exec_shell"}, False)

    # State changing action without opt-in
    assert "allow_state_change=true" in counter.order_gate("delete_index", catalog, False)

    # State changing action with opt-in
    assert counter.order_gate("delete_index", catalog, True) is None

    # Normal read action
    assert counter.order_gate("search_sections", catalog, False) is None


def test_examples_validation():
    """Verify every example key in EXAMPLES corresponds to a live action and satisfies required args."""
    tools = {t.name: t for t in server._all_tools()}
    for action, ex in counter.EXAMPLES.items():
        assert action in tools, f"Example action '{action}' not found in live tools catalog."
        tool = tools[action]
        schema = tool.inputSchema or {}
        req = schema.get("required", [])
        for r in req:
            assert r in ex, f"Example for '{action}' missing required schema argument '{r}'."


def test_search_catalog():
    catalog = server._catalog_rows()
    # Empty query returns catalog up to limit
    res = counter.search_catalog(catalog, "", limit=5)
    assert len(res) <= 5

    # Specific query for search sections
    res_search = counter.search_catalog(catalog, "search sections", limit=5)
    assert len(res_search) > 0
    actions = [r["action"] for r in res_search]
    assert "search_sections" in actions


def test_classify_intent():
    catalog = server._catalog_names()

    # Intent mapping for search
    recs = counter.classify_intent("search sections for authentication", catalog)
    assert len(recs) > 0
    assert recs[0]["action"] == "search_sections"

    # Intent mapping for broken links
    recs_links = counter.classify_intent("check for broken links", catalog)
    assert len(recs_links) > 0
    assert recs_links[0]["action"] == "get_broken_links"


@pytest.mark.asyncio
async def test_handle_menu():
    res = server._handle_menu({"query": "search"})
    assert len(res) == 1
    data = json.loads(res[0].text)
    assert data["tool"] == "menu"
    assert data["count"] > 0
    assert "actions" in data
    first_action = data["actions"][0]
    assert "name" in first_action
    assert "summary" in first_action


@pytest.mark.asyncio
async def test_handle_get_tool_details():
    res = server._handle_get_tool_details({"name": "search_sections"})
    assert len(res) == 1
    data = json.loads(res[0].text)
    assert data["name"] == "search_sections"
    assert "signature" in data
    assert data["signature"].startswith("search_sections(")
    assert "summary" in data
    assert "description" in data
    assert "parameters" in data
    assert "required" in data
    assert "state_changing" in data


def test_schema_minification_and_ts_signature():
    from jdocmunch_mcp.schema_minifier import (
        json_schema_to_typescript_signature,
        minify_description,
        minify_json_schema,
    )
    desc = "  Search documentation sections. (v2.0)\n @example search_sections('auth')\n @param query The query"
    min_desc = minify_description(desc)
    assert "@example" not in min_desc
    assert "@param" not in min_desc
    assert "(v2.0)" not in min_desc
    assert min_desc == "Search documentation sections."

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SearchSections",
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"}
        },
        "required": ["query"]
    }
    min_schema = minify_json_schema(schema)
    assert "$schema" not in min_schema
    assert "title" not in min_schema

    sig = json_schema_to_typescript_signature("search_sections", schema)
    assert sig == "search_sections(query: string, limit?: number): any"


@pytest.mark.asyncio
async def test_handle_get_tool_details_unknown():
    res = server._handle_get_tool_details({"name": "nonexistent_action_xyz"})
    assert len(res) == 1
    data = json.loads(res[0].text)
    assert "error" in data


@pytest.mark.asyncio
async def test_handle_route():
    res = await server._handle_route({"task": "search documentation for authentication", "repo": "test/repo"})
    assert len(res) == 1
    data = json.loads(res[0].text)
    assert data["tool"] == "route"
    assert len(data["recommended"]) > 0
    assert data["recommended"][0]["action"] == "search_sections"

