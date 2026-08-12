"""Tests for v1.52.0: roles / exclude_roles plural ANY-match filters."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_with_roles(tmp_path):
    """Index sections with role-classifiable headings."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "g.md").write_text(textwrap.dedent("""
        # Title

        ## Troubleshooting connection errors

        Connection refused — check the port. Common fix is to restart.

        ## How to install

        Install the package via pip and verify with foo --version.

        ## API Reference

        Endpoint listing for /v1/users.

        ## Concept Background

        Some prose explanation about the system architecture.

        ## Examples

        Sample usage code with curl.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="rl",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestRolesFilters:
    def _roles_in_results(self, out: dict) -> set:
        return {((r.get("metadata") or {}).get("role") or "").lower()
                for r in out["results"]}

    def test_default_returns_all(self, tmp_path):
        _index_with_roles(tmp_path)
        out = search_sections(repo="rl", query="install configure",
                              semantic=False, storage_path=str(tmp_path))
        assert "roles_filter" not in out["_meta"]
        assert "exclude_roles_filter" not in out["_meta"]

    def test_roles_any_match_include(self, tmp_path):
        _index_with_roles(tmp_path)
        out = search_sections(
            repo="rl", query="install configure connection",
            roles=["how_to", "troubleshooting"],
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            role = ((r.get("metadata") or {}).get("role") or "").lower()
            assert role in {"how_to", "troubleshooting"}, role
        assert set(out["_meta"]["roles_filter"]) == {"how_to", "troubleshooting"}

    def test_exclude_roles_any_match(self, tmp_path):
        _index_with_roles(tmp_path)
        out = search_sections(
            repo="rl", query="install configure",
            exclude_roles=["how_to"],
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            role = ((r.get("metadata") or {}).get("role") or "").lower()
            assert role != "how_to"
        assert out["_meta"]["exclude_roles_filter"] == ["how_to"]

    def test_roles_and_exclude_roles_stack(self, tmp_path):
        _index_with_roles(tmp_path)
        # Allow how_to/example, exclude example → only how_to remains.
        out = search_sections(
            repo="rl", query="install configure example",
            roles=["how_to", "example"],
            exclude_roles=["example"],
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            role = ((r.get("metadata") or {}).get("role") or "").lower()
            assert role == "how_to", role

    def test_singular_role_unchanged(self, tmp_path):
        _index_with_roles(tmp_path)
        # Existing singular `role=` keeps working as a hard filter.
        out = search_sections(
            repo="rl", query="install", role="how_to",
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            assert ((r.get("metadata") or {}).get("role") or "") == "how_to"

    def test_case_insensitive(self, tmp_path):
        _index_with_roles(tmp_path)
        out = search_sections(
            repo="rl", query="install", roles=["HOW_TO"],
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            role = ((r.get("metadata") or {}).get("role") or "").lower()
            assert role == "how_to"

    def test_empty_lists_treated_as_off(self, tmp_path):
        _index_with_roles(tmp_path)
        out = search_sections(
            repo="rl", query="install configure",
            roles=[], exclude_roles=[],
            semantic=False, storage_path=str(tmp_path),
        )
        assert "roles_filter" not in out["_meta"]
        assert "exclude_roles_filter" not in out["_meta"]


class TestSchema:
    def test_role_filter_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "roles" in props
        assert "exclude_roles" in props
        assert props["roles"]["type"] == "array"
        assert props["exclude_roles"]["type"] == "array"
