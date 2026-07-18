"""Tests for the MCP server module."""

import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from jdocmunch_mcp.server import list_tools, call_tool


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_63_tools(self):
        tools = await list_tools()
        assert len(tools) == 63

    @pytest.mark.asyncio
    async def test_tool_names(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        expected = {
            "index_local", "doc_index_repo", "doc_list_repos", "doc_resolve_repo", "list_docs",
            "get_doc", "get_index_overview",
            "get_toc", "get_toc_tree", "get_document_outline",
            "search_sections", "search_titles", "count_sections",
            "get_section", "get_sections", "get_section_context",
            "section_neighbors", "describe_section",
            "get_section_summary", "get_section_summaries",
            "get_orphan_sections", "get_section_path", "get_section_excerpt",
            "get_section_excerpts", "get_section_descendants", "get_all_tags",
            "get_all_roles", "get_recent_changes", "delete_index",
            "get_broken_links", "get_doc_coverage",
            "get_backlinks", "get_stale_pages", "get_wiki_stats",
            "analyze_perf", "get_session_stats", "check_embedding_drift",
            "find_code_examples", "link_code_to_symbols",
            "resolve_related_code_repos",
            "find_endpoint", "list_endpoints_by_tag", "find_operations_using_schema", "get_schema_graph",
            "lookup_term", "list_terms",
            "get_related_sections", "get_section_diff", "get_doc_health",
            "get_tutorial_path", "get_undocumented_symbols",
            "tune_weights",
            "list_repo_groups", "define_repo_group",
            "verify_index",
            "check_section_delete_safe",
            "get_section_blast_radius",
            "find_similar_sections",
            "doc_health_radar",
            "diff_doc_health_radar",
            "get_doc_pr_risk_profile",
            "get_watch_status",
            "jdocmunch_guide",
        }
        assert names == expected

    @pytest.mark.asyncio
    async def test_each_tool_has_schema(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.inputSchema is not None
            assert "type" in tool.inputSchema

    @pytest.mark.asyncio
    async def test_required_fields_defined(self):
        tools = await list_tools()
        # Tools that need 'repo' should have it in required.
        # Tools without arguments (e.g. doc_list_repos, analyze_perf,
        # get_session_stats) legitimately have no 'required' clause.
        # search_sections requires only 'query' as of v1.26 (repo_group is
        # an alternative to repo). define_repo_group requires name+repos.
        no_repo_required = {
            "index_local", "doc_index_repo", "doc_list_repos",
            "doc_resolve_repo",
            "analyze_perf", "get_session_stats", "check_embedding_drift",
            "tune_weights",
            "list_repo_groups", "define_repo_group",
            "search_sections",
            "diff_doc_health_radar",
            "get_watch_status",
            "jdocmunch_guide",
        }
        for tool in tools:
            if tool.name not in no_repo_required:
                assert "required" in tool.inputSchema
                assert "repo" in tool.inputSchema["required"]


class TestCallTool:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await call_tool("nonexistent_tool", {})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_list_repos_no_storage(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
        result = await call_tool("doc_list_repos", {})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert "repos" in data
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_index_local_invalid_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
        result = await call_tool("index_local", {"path": "/nonexistent/path"})
        data = json.loads(result[0].text)
        assert data["success"] is False
