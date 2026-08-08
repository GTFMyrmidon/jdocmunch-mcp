"""Tests for server-side argument normalization and parameter coercion in jdocmunch-mcp."""

import json
import pytest
from jdocmunch_mcp import server


class TestOrderActionProperties:
    def test_known_action_has_properties(self):
        props = server._order_action_properties("get_doc")
        assert "doc_path" in props
        assert "repo" in props

    def test_unknown_action_empty(self):
        assert server._order_action_properties("no_such_action_xyz") == {}


class TestNormalizeOrderArgs:
    def test_path_maps_to_doc_path_for_get_doc(self):
        out = server._normalize_order_args(
            "get_doc", {"repo": "owner/name", "path": "docs/readme.md"}
        )
        assert out == {"repo": "owner/name", "doc_path": "docs/readme.md"}

    def test_file_maps_to_doc_path_for_get_doc(self):
        out = server._normalize_order_args(
            "get_doc", {"repo": "owner/name", "file": "docs/readme.md"}
        )
        assert out == {"repo": "owner/name", "doc_path": "docs/readme.md"}

    def test_doc_maps_to_doc_path(self):
        out = server._normalize_order_args(
            "get_doc", {"repo": "owner/name", "doc": "docs/readme.md"}
        )
        assert out == {"repo": "owner/name", "doc_path": "docs/readme.md"}

    def test_section_maps_to_section_id(self):
        out = server._normalize_order_args(
            "get_section", {"repo": "owner/name", "section": "docs/readme.md#installation"}
        )
        assert out == {"repo": "owner/name", "section_id": "docs/readme.md#installation"}

    def test_id_maps_to_section_id_for_get_section(self):
        out = server._normalize_order_args(
            "get_section", {"repo": "owner/name", "id": "docs/readme.md#installation"}
        )
        assert out == {"repo": "owner/name", "section_id": "docs/readme.md#installation"}

    def test_text_maps_to_query_for_search_sections(self):
        out = server._normalize_order_args(
            "search_sections", {"repo": "owner/name", "text": "authentication"}
        )
        assert out == {"repo": "owner/name", "query": "authentication"}

    def test_declared_keys_untouched(self):
        args = {"repo": "owner/name", "doc_path": "docs/readme.md"}
        assert server._normalize_order_args("get_doc", args) == args

    def test_alias_not_applied_when_target_present(self):
        args = {"repo": "r", "doc_path": "docs/a.md", "path": "docs/b.md"}
        out = server._normalize_order_args("get_doc", args)
        assert out["doc_path"] == "docs/a.md"
        assert out["path"] == "docs/b.md"

    def test_scalar_coerced_to_list_for_array_prop(self):
        out = server._normalize_order_args(
            "get_sections", {"repo": "owner/name", "ids": "docs/readme.md#install"}
        )
        assert out == {"repo": "owner/name", "section_ids": ["docs/readme.md#install"]}

    def test_single_item_list_unwrapped_for_string_prop(self):
        out = server._normalize_order_args(
            "get_doc", {"repo": "owner/name", "path": ["docs/readme.md"]}
        )
        assert out == {"repo": "owner/name", "doc_path": "docs/readme.md"}

    def test_unknown_action_returns_args_unchanged(self):
        args = {"anything": 1}
        assert server._normalize_order_args("no_such_action_xyz", args) is args


class TestCoerceArguments:
    def test_boolean_coercion(self):
        schema = {"properties": {"use_ai_summaries": {"type": "boolean"}}}
        assert server._coerce_arguments({"use_ai_summaries": "true"}, schema) == {"use_ai_summaries": True}
        assert server._coerce_arguments({"use_ai_summaries": "false"}, schema) == {"use_ai_summaries": False}

    def test_integer_coercion(self):
        schema = {"properties": {"limit": {"type": "integer"}}}
        assert server._coerce_arguments({"limit": "10"}, schema) == {"limit": 10}

    def test_number_coercion(self):
        schema = {"properties": {"weight": {"type": "number"}}}
        assert server._coerce_arguments({"weight": "3.14"}, schema) == {"weight": 3.14}


class TestHandleOrderNormalizes:
    @pytest.mark.asyncio
    async def test_order_dispatches_normalized_args(self, monkeypatch):
        seen = {}

        async def fake_call_tool(name, arguments):
            seen["name"] = name
            seen["arguments"] = arguments
            from mcp.types import TextContent

            return [TextContent(type="text", text=json.dumps({"ok": True}))]

        monkeypatch.setattr(server, "call_tool", fake_call_tool)
        await server._handle_order(
            {"action": "get_doc", "args": {"repo": "r", "path": "docs/a.md"}}
        )
        assert seen["name"] == "get_doc"
        assert seen["arguments"] == {"repo": "r", "doc_path": "docs/a.md"}
